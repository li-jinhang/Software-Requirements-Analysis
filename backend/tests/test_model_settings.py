"""Exercise user-configured providers without using a personal subscription or API key."""

import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from app.services import model_profiles

    monkeypatch.setattr(model_profiles.settings, "data_dir", tmp_path)
    monkeypatch.setattr(model_profiles.settings, "mock_llm", True)
    return model_profiles


def test_bindings_key_redaction_and_provider_errors(profiles, monkeypatch):
    from app.services.model_connection import ModelConnection

    body = profiles.ProfileInput(
        name="first",
        mode="api",
        model="model-a",
        base_url="https://a.example/v1",
        api_key="fixture-key",
    )
    first = profiles.save_profile(body)
    second = profiles.save_profile(
        body.model_copy(update={"name": "second", "model": "model-b", "api_key": ""})
    )
    profiles.bind("report", second)
    assert profiles.resolve("extract")["id"] == first
    assert profiles.resolve("report")["id"] == second
    assert "fixture-key" not in json.dumps(profiles.dashboard())
    with pytest.raises(profiles.ModelError):
        profiles.save_profile(
            body.model_copy(update={"api_key": None, "base_url": "https://other.example/v1"}), first
        )
    with pytest.raises(profiles.ModelError):
        profiles.delete_profile(first)
    profiles.save_profile(body.model_copy(update={"name": "updated", "api_key": None}), first)
    assert profiles.resolve("feynman")["api_key"] == "fixture-key"
    calls = []

    def handler(req):
        calls.append(req.url.path)
        assert req.headers["authorization"] == "Bearer fixture-key"
        if req.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "OK"}]}
                    ]
                },
            )
        return httpx.Response(405)

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    assert ModelConnection(profiles.resolve("extract")).text("test", "test") == "OK"
    assert calls == ["/v1/chat/completions", "/v1/responses"]
    profiles.bind("report", None)
    assert profiles.resolve("report")["id"] == first
    profiles.delete_profile(second)


def test_browser_login_refresh_and_codex_transport(profiles, monkeypatch):
    from app.services import chatgpt_login as login
    from app.services.model_connection import ModelConnection

    real_server = login.ThreadingHTTPServer
    monkeypatch.setattr(
        login, "ThreadingHTTPServer", lambda addr, handler: real_server(("127.0.0.1", 0), handler)
    )
    exchanges = []

    def exchange(url, *, data, timeout):
        exchanges.append(data)
        claims = {
            "exp": int(time.time()) + (45 if len(exchanges) == 1 else 3600),
            "https://api.openai.com/auth": {"chatgpt_account_id": "test-account"},
        }
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return httpx.Response(
            200,
            json={
                "access_token": "header." + payload + ".signature",
                "refresh_token": "fixture-refresh",
            },
        )

    monkeypatch.setattr(httpx, "post", exchange)
    try:
        url = login.begin()
        fields = parse_qs(urlsplit(url).query)
        port = login._pending["server"].server_port
        callback = f"http://127.0.0.1:{port}/auth/callback"
        headers = {"Host": "localhost:1455"}
        with httpx.Client(trust_env=False) as client:
            assert (
                client.get(
                    callback, params={"state": "wrong", "code": "fixture"}, headers=headers
                ).status_code
                == 400
            )
            assert not exchanges
            assert client.get(
                callback, params={"state": fields["state"][0], "code": "fixture"}, headers=headers
            ).is_success
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(exchanges[0]["code_verifier"].encode()).digest()
            )
            .decode()
            .rstrip("=")
        )
        assert challenge == fields["code_challenge"][0]
        assert login.status() == {"connected": True, "pending": False, "error": ""}

        def handler(req):
            assert (
                req.url.path == "/backend-api/codex/responses"
                and req.headers["chatgpt-account-id"] == "test-account"
            )
            assert json.loads(req.content)["store"] is False
            return httpx.Response(
                200,
                text='data: {"type":"response.output_text.delta","delta":"OK"}\n\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n',
            )

        original = httpx.Client
        monkeypatch.setattr(
            httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
        )
        assert ModelConnection({"mode": "chatgpt", "model": "demo"}).text("test", "test") == "OK"
        assert exchanges[-1]["grant_type"] == "refresh_token"
    finally:
        login.end_login(logout=True)
    assert not login.status()["connected"]


def test_truncated_graph_uses_bound_model_for_smaller_units(profiles):
    from app.services.llm_client import live_extract
    from app.services.model_connection import ModelConnection, StructuredOutputError

    names = ["位置", "位移", "路程", "速度", "速率", "加速度"]
    calls = []

    class Limited(ModelConnection):
        def structured(self, schema, task, material):
            calls.append(schema.__name__)
            if schema.__name__ == "Extraction":
                raise StructuredOutputError("output limit")
            if schema.__name__ == "Outline":
                return schema(summary="课程摘要", names=names)
            if schema.__name__ == "Definition":
                term = json.loads(material)["指定概念"]
                return schema(summary=term + "的定义", key_point=term + "的例子", importance=3)
            return schema(
                links=[
                    dict(pair="1>3", relation_type="prerequisite", description="速度定义依赖位移")
                ]
            )

    graph = live_extract(Limited({}), "直线运动课程资料", "运动学")
    assert calls == ["Extraction", "Outline"] + ["Definition"] * 6 + ["CourseLinks"]
    assert [n.concept_name for n in graph.nodes] == names
    assert (graph.edges[0].source, graph.edges[0].target) == ("位移", "速度")
