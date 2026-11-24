"""Opt-in, real-model Edge acceptance using a temporary course DB and the current model bindings."""

from contextlib import ExitStack, closing
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from playwright.sync_api import expect, sync_playwright
from live_lookup import ROOT, port, serve


def run():
    backend, frontend = ROOT / "backend", ROOT / "frontend"
    python = backend / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with tempfile.TemporaryDirectory(prefix="feynman-cognitive-") as work, ExitStack() as stack:
        directory = Path(work)
        source = (
            Path(os.environ.get("DATA_DIR", backend / "data")) / "model-settings/profiles.sqlite3"
        )
        if source.is_file():
            dest = directory / "model-settings/profiles.sqlite3"
            dest.parent.mkdir()
            with closing(
                sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
            ) as src, closing(sqlite3.connect(dest)) as copy:
                src.backup(copy)
        api_port, ui_port = port(), port()
        api, url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{ui_port}"
        log = stack.enter_context((directory / "servers.log").open("wb"))
        serve(
            stack,
            [python, "-m", "uvicorn", "app.main:app", "--port", str(api_port)],
            backend,
            {"DATA_DIR": work, "SEED_DEMO": "true", "CORS_ORIGINS": json.dumps([url])},
            api + "/docs",
            log,
        )
        serve(
            stack,
            [
                shutil.which("node"),
                "node_modules/vite/bin/vite.js",
                "--host",
                "127.0.0.1",
                "--port",
                str(ui_port),
                "--strictPort",
            ],
            frontend,
            {"VITE_BACKEND_URL": api},
            url,
            log,
        )
        pw = stack.enter_context(sync_playwright())
        browser = pw.chromium.launch(channel="msedge")
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(url)
        button = lambda name: page.get_by_role("button", name=name, exact=True)
        page.locator(".account button").click()
        page.get_by_label("用户名").fill("student")
        page.get_by_label("密码", exact=True).fill("123456")
        button("登录").click()
        expect(page.locator(".account")).to_contain_text("在线服务")
        button("输入知识点生成").click()
        page.get_by_label("知识点名称").fill("有序数组上的二分查找")
        page.get_by_label("学科（选填）").fill("数据结构")
        started = time.monotonic()
        with page.expect_response(
            lambda r: r.url.endswith("/generate-from-topic"), timeout=300000
        ) as created:
            button("生成认知文档").click()
        assert created.value.status == 201, (created.value.status, created.value.text()[:300])
        print("生成课程", round(time.monotonic() - started, 1), "s", flush=True)
        page.locator(".card").filter(has_text="有序数组上的二分查找").get_by_role(
            "button", name="开讲"
        ).click()
        button("开始教学").click()
        trigger = page.get_by_role("button", name="Agent 认知文档", exact=False)
        trigger.click()
        target = page.locator(".rubric-item").filter(has_text="当前提问")
        first_id = target.get_attribute("data-item-id")
        total = page.locator(".rubric-item").count()
        assert 8 <= total <= 14
        expect(target.locator("details")).to_contain_text("判定标准")
        page.get_by_label("关闭", exact=True).click()
        page.get_by_label("收起百科").click()
        lesson = page.get_by_label("讲解内容")
        lesson.fill(
            "我认为这条结论恰好相反。二分查找在完全无序的数组中也总能直接比较一次就找到目标，每次只排除一个元素。"
        )
        started = time.monotonic()
        button("发送讲解").click()
        card = page.locator(".p1 .correction-card").first
        expect(card).to_be_visible(timeout=240000)
        for label in ("你的表述", "标准解读与举例", "参考依据"):
            expect(card).to_contain_text(label)
        print("右侧自动纠偏", round(time.monotonic() - started, 1), "s", flush=True)
        trigger.click()
        row = page.locator(f'.rubric-item[data-item-id="{first_id}"]')
        expect(row).to_have_class(re.compile("wrong|partial"), timeout=20000)
        print("首条标记", row.get_attribute("class"), flush=True)
        page.get_by_label("关闭", exact=True).click()
        page.reload()
        expect(card).to_be_visible(timeout=240000)
        explanations = [
            c.locator("h4", has_text="标准解读与举例")
            .locator("xpath=following-sibling::p[1]")
            .inner_text()
            for c in page.locator(".p1 .correction-card").all()
        ]
        card.get_by_role("button").last.click()
        lesson.fill(
            "我把刚才的结论改正一下。"
            + " ".join(explanations)
            + "。例如在升序数组 [1,3,5,7,9] 中查找 7，先以 O(1) 下标访问中点 5。因为左边所有值都小于 7，"
            "可以把左边界更新到 mid+1，在右半区间继续。无序数组无法安全排除半区，有序链表访问中点需要遍历。"
        )
        started = time.monotonic()
        button("发送讲解").click()
        trigger.click()
        expect(row).to_have_class(re.compile("mastered"), timeout=240000)
        expect(target).to_have_count(1)
        assert target.get_attribute("data-item-id") != first_id
        print(
            "修正标绿并推进",
            round(time.monotonic() - started, 1),
            "s，条目数",
            total,
            "，下一条",
            target.get_attribute("data-item-id"),
            flush=True,
        )
        page.get_by_label("关闭", exact=True).click()
        assert not errors, errors


if __name__ == "__main__":
    run()
