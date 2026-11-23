"""Opt-in live Edge acceptance. Starts isolated servers and uses the configured LLM."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from contextlib import ExitStack

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(stack, args, cwd, env, url, log):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(args, cwd=cwd, env={**os.environ, **env}, stdout=log, stderr=log,
                               creationflags=flags)
    def stop():
        if process.poll() is None:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=20)
    stack.callback(stop)
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError("Test server exited")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Test server startup timed out")


def run():
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        backend, frontend = ROOT / "backend", ROOT / "frontend"
        api_port = port()
        base = f"http://127.0.0.1:{api_port}"
        ui_port = port()
        url = f"http://127.0.0.1:{ui_port}"
        log_path = Path(tmp) / "servers.log"
        log = stack.enter_context(log_path.open("wb"))
        python = backend / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        serve(stack, [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                      "--port", str(api_port)], backend,
              {"DATA_DIR": tmp, "MOCK_LLM": "false", "SEED_DEMO": "true",
               "PYTHONIOENCODING": "utf-8", "CORS_ORIGINS": json.dumps([url])}, base + "/docs", log)
        serve(stack, [shutil.which("node"), "node_modules/vite/bin/vite.js", "--host", "127.0.0.1",
                      "--port", str(ui_port), "--strictPort"], frontend,
              {"VITE_BACKEND_URL": base}, url, log)
        pw = stack.enter_context(sync_playwright())
        browser = pw.chromium.launch(channel="msedge")
        stack.callback(browser.close)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.locator(".account button").click()
        page.get_by_label("用户名").fill("student")
        page.get_by_label("密码", exact=True).fill("123456")
        btn = lambda name: page.get_by_role("button", name=name, exact=True)
        btn("登录").click()
        expect(page.locator(".account")).to_contain_text("在线服务")
        btn("开讲").first.click()
        btn("开始教学").click()
        for i, question in enumerate(["损失函数怎样用 MSE 举一个数值例子？",
                                      "梯度下降时学习率过大为什么会震荡？请用数值举例？"]):
            page.get_by_label("讲解内容").fill(question)
            btn("发送讲解").click()
            listener = page.locator(".p0 .msg:not(.mine)").last
            expect(page.locator(".p0 .msg:not(.mine)")).to_have_count(i + 1)
            if i:
                expect(listener).to_contain_text("100%")
                expect(listener).to_contain_text("「过拟合」")
            start = time.monotonic()
            listener.get_by_role("button", name="去右侧查证").click()
            replies = page.locator(".p1 article:not(.mine):has(.source-list)")
            expect(replies).to_have_count(i + 1, timeout=150000)
            reply = replies.last
            expect(reply).to_contain_text("课件溯源")
            expect(reply).to_contain_text("学科知识补充与举例")
            expect(reply).to_contain_text(re.compile("均方|MSE" if not i else "震荡|振荡"))
            expect(reply).to_contain_text(re.compile(r"\d+\.\d+|[0-9]\s*[→=]"))
            expect(reply).not_to_contain_text("这个片段没有直接回答")
            # Require a generated response, independent of the authored offline fallback.
            expect(reply).not_to_contain_text("回到左窗，用自己的话解释例子中哪个条件改变了结果。")
            summary = reply.locator(".source-list > summary")
            summary.click()
            cards = reply.locator(".source-card")
            titles = []
            for card in cards.all():
                titles.append(card.locator("header").inner_text().replace("\n", " "))
                assert float(card.locator("header small").inner_text().split()[-1]) >= 0.12
                heading = card.locator("h2").inner_text()
                assert "正则化" not in heading
                visible = card.inner_text()
                assert not re.search(r"(?m)^\s*#{1,6}\s|\$[^$]+\$", visible), visible
            if i:
                assert cards.locator(".katex-html").count() > 0
            print(question, round(time.monotonic() - start, 1), "s", titles, flush=True)
            print("公式:", reply.locator("annotation").all_text_contents()[:14], flush=True)
        page.reload()
        expect(page.locator(".p1 .source-list")).to_have_count(2)
        assert page.request.get(url + "/favicon.svg").ok and not errors, errors


if __name__ == "__main__":
    run()
