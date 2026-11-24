"""Browser PKCE login on the registered loopback redirect. No existing app credentials are imported."""

import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.services.model_profiles import ModelError, account_data, save_account

CLIENT = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH = "https://auth.openai.com"
REDIRECT = "http://localhost:1455/auth/callback"
_lock = threading.RLock()
_pending = None
_last_error = ""


def exchange(fields, previous=None):
    try:
        r = httpx.post(AUTH + "/oauth/token", data={"client_id": CLIENT, **fields}, timeout=30)
        if not r.is_success:
            if previous and (
                r.status_code == 401 or (r.status_code == 400 and "invalid_grant" in r.text)
            ):
                save_account(None)
            raise ModelError(f"ChatGPT 授权失败（HTTP {r.status_code}），请重新登录")
        body = r.json()
        access = body["access_token"]
        payload = access.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        account = claims["https://api.openai.com/auth"]["chatgpt_account_id"]
        refresh = body.get("refresh_token") or (previous or {}).get("refresh")
        if (
            not refresh
            or not account
            or claims["exp"] <= time.time() + 30
            or previous
            and previous["account"] != account
        ):
            raise ModelError("ChatGPT 凭据无效，请重新登录")
        return dict(access=access, refresh=refresh, account=account, expires=claims["exp"])
    except (httpx.RequestError, KeyError, TypeError, IndexError, ValueError) as exc:
        if isinstance(exc, ModelError):
            raise
        raise ModelError("无法取得有效的 ChatGPT 登录凭据") from None


def credentials(rejected=None, expected_account=None):
    with _lock:
        saved = account_data()
        if not saved:
            raise ModelError("请先在模型配置中登录 ChatGPT", 409)
        if expected_account and expected_account != saved["account"]:
            raise ModelError("ChatGPT 账号已切换，请重新发起请求")
        if saved["expires"] < time.time() + 90 or rejected == saved["access"]:
            saved = exchange(
                {"grant_type": "refresh_token", "refresh_token": saved["refresh"]}, saved
            )
            save_account(saved)
        return saved["access"], saved["account"]


def status():
    with _lock:
        return {"connected": bool(account_data()), "pending": bool(_pending), "error": _last_error}


def end_login(logout=False):
    global _pending, _last_error
    with _lock:
        if _pending:
            _pending["server"].shutdown()
            _pending["server"].server_close()
            _pending["timer"].cancel()
            _pending = None
        _last_error = ""
        if logout:
            save_account(None)


def begin():
    global _pending, _last_error
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )

    def finish(error="", saved=None):
        global _last_error
        with _lock:
            if not _pending or _pending["state"] != state:
                return False
            if saved:
                save_account(saved)
            end_login()
            _last_error = error
            return True

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            valid = (
                self.headers.get("Host") in {"localhost:1455", "127.0.0.1:1455"}
                and url.path == "/auth/callback"
                and query.get("state") == [state]
            )
            if not valid:
                self.send_error(400, "Invalid callback")
                return
            with _lock:
                if not _pending or _pending["state"] != state or _pending.get("processing"):
                    self.send_error(409, "Callback already handled")
                    return
                _pending["processing"] = True
            try:
                code = query.get("code", [""])[0]
                if not code or "error" in query:
                    raise ModelError("授权已取消")
                saved = exchange(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "code_verifier": verifier,
                        "redirect_uri": REDIRECT,
                    }
                )
                if not finish(saved=saved):
                    raise ModelError("登录请求已结束")
                message = "登录成功，可以返回费曼工作台。"
            except ModelError as exc:
                finish(str(exc))
                message = "登录未完成，请返回工作台查看状态。"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(message.encode())

    with _lock:
        end_login()
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 1455), Callback)
        except OSError:
            raise ModelError("本机回调端口 1455 已被占用，请结束其他登录流程") from None
        timer = threading.Timer(180, lambda: finish("登录已超时，请重试"))
        timer.daemon = True
        _pending = {"state": state, "server": server, "timer": timer}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        timer.start()
    return (
        AUTH
        + "/oauth/authorize?"
        + urlencode(
            dict(
                client_id=CLIENT,
                response_type="code",
                redirect_uri=REDIRECT,
                scope="openid profile email offline_access",
                state=state,
                code_challenge_method="S256",
                code_challenge=challenge,
                id_token_add_organizations="true",
                codex_cli_simplified_flow="true",
                originator="FeynmanMind",
            )
        )
    )
