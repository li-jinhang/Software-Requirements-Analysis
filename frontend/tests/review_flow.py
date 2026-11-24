"""Run the review and correction-flight acceptance flow in Edge with isolated fixture inference."""
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile

from playwright.sync_api import expect, sync_playwright
from live_lookup import ROOT, port, serve


def run():
    with tempfile.TemporaryDirectory(prefix="feynman-review-") as work, ExitStack() as stack:
        log = stack.enter_context((Path(work) / "servers.log").open("wb"))
        backend, frontend = ROOT / "backend", ROOT / "frontend"
        api_port, ui_port = port(), port()
        api, url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{ui_port}"
        py = backend / ".venv" / "Scripts/python.exe"
        if not py.exists():
            py = backend / ".venv/bin/python"
        serve(stack, [py, "-c", "import sys, uvicorn; sys.path.insert(0, 'tests'); "
              "from fixture_inference import install; install(); "
              "uvicorn.run('app.main:app', host='127.0.0.1', port=int(sys.argv[1]))", str(api_port)],
              backend, {"DATA_DIR": work, "SEED_DEMO": "true", "MOCK_LLM": "true",
                        "OPENAI_API_KEY": "", "CORS_ORIGINS": json.dumps([url])}, api + "/docs", log)
        serve(stack, [shutil.which("node"), "node_modules/vite/bin/vite.js", "--host", "127.0.0.1",
                      "--port", str(ui_port), "--strictPort"], frontend, {"VITE_BACKEND_URL": api}, url, log)
        pw = stack.enter_context(sync_playwright())
        browser = pw.chromium.launch(channel="msedge")
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 980}, reduced_motion="no-preference")
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        # Observe the transient DOM and the browser's actual animation effects on every page load.
        page.add_init_script(r"""window.flights = [];
          new MutationObserver(records => {
            for (const record of records) for (const bar of record.addedNodes) {
              if (!bar.classList?.contains('correction-flight')) continue;
              const style = getComputedStyle(bar);
              const card = [...document.querySelectorAll('.p1 .correction-card')].at(-1);
              const reveal = card.getAnimations().find(a => a.effect.getKeyframes()[0].clipPath);
              const frames = reveal?.effect.getKeyframes() ?? [];
              const reply = [...document.querySelectorAll('.p0 .msg.mine')].at(-1);
              const path = bar.style.offsetPath;
              const coords = path.match(/-?[\d.]+/g).map(Number);
              const seam = document.querySelector('.splitter')?.getBoundingClientRect().x;
              window.flights.push({path, crosses: coords[0] < seam && coords.at(-2) > seam,
                height: bar.offsetHeight, text: bar.textContent, shadow: style.boxShadow,
                travel: bar.getAnimations()[0]?.effect.getTiming().duration,
                delay: reveal?.effect.getTiming().delay,
                folded: frames[0]?.clipPath, opened: frames.at(-1)?.clipPath,
                outline: reply?.getAnimations().some(a => a.effect.getKeyframes().some(k => k.outlineColor))});
            }
          }).observe(document, {childList:true, subtree:true});
        """)
        page.goto(url)
        button = lambda name: page.get_by_role("button", name=name, exact=True)
        page.locator(".account button").click()
        page.get_by_label("用户名").fill("student")
        page.get_by_label("密码", exact=True).fill("123456")
        button("登录").click()
        expect(page.locator(".account")).to_contain_text("在线服务")
        button("开讲").first.click()
        button("开始教学").click()
        rubric = page.get_by_role("button", name="Agent 认知文档", exact=False)
        rubric.click()
        button("复习此条").first.click()
        expect(page.get_by_text("正在复习选定条目", exact=True)).to_be_visible()
        page.get_by_label("收起百科").click()
        page.get_by_label("讲解内容").fill("梯度变大")
        button("发送讲解").click()
        expect(page.locator(".p1 .correction-card")).to_be_visible()
        expect(page.get_by_text("本轮复习已完成", exact=True)).to_be_visible()
        page.wait_for_function("window.flights.length === 1")
        flight = page.evaluate("window.flights[0]")
        assert flight["outline"] and flight["crosses"] and "Q" in flight["path"], flight
        assert 6 <= flight["height"] <= 8 and not flight["text"] and flight["shadow"] == "none"
        assert 0 < flight["travel"] <= 500 and abs(flight["delay"] - flight["travel"]) <= 20
        assert flight["folded"].startswith("inset(") and flight["opened"].startswith("inset(0px")
        assert flight["folded"] != flight["opened"]
        expect(page.locator(".correction-flight")).to_have_count(0)
        page.reload()
        expect(page.locator(".p1 .correction-card")).to_be_visible()
        expect(page.get_by_text("本轮复习已完成", exact=True)).to_be_visible()
        assert page.evaluate("window.flights.length") == 0
        button("模拟 +1 天").click()
        reminder = page.get_by_label("记忆曲线复习提醒")
        expect(reminder).to_be_visible()
        button("跳过").click()
        expect(reminder).to_have_count(0)
        button("自主复习").click()
        expect(button("复习全部薄弱项")).to_be_enabled()
        expect(button("复习全部已学项")).to_be_enabled()
        page.get_by_role("checkbox").nth(1).check()
        page.get_by_role("checkbox").nth(2).check()
        with page.expect_response(lambda r: r.url.endswith("/sessions") and r.request.method == "POST") as created:
            button("开始复习选中项").click()
        assert len(created.value.json()["target_item_ids"]) == 2
        expect(page.get_by_text("正在复习选定条目", exact=True)).to_be_visible()
        button("模拟 +1 天").click()
        expect(reminder).to_be_visible()
        with page.expect_response(lambda r: r.url.endswith("/sessions") and r.request.method == "POST") as reminder_session:
            button("跟他复习").click()
        assert len(reminder_session.value.json()["target_item_ids"]) == 1
        expect(rubric).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_label("讲解内容").fill("梯度变大")
        button("发送讲解").click()
        expect(page.locator(".p1 .correction-card")).to_be_visible()
        expect(page.get_by_text("本轮复习已完成", exact=True)).to_be_visible()
        assert page.evaluate("window.flights.length") == 0
        page.set_viewport_size({"width": 1440, "height": 980})
        page.emulate_media(reduced_motion="reduce")
        rubric.click()
        button("复习此条").first.click()
        expect(page.get_by_text("正在复习选定条目", exact=True)).to_be_visible()
        page.get_by_label("讲解内容").fill("梯度变大")
        button("发送讲解").click()
        expect(page.locator(".p1 .correction-card")).to_be_visible()
        expect(page.get_by_text("本轮复习已完成", exact=True)).to_be_visible()
        assert page.evaluate("window.flights.length") == 0
        assert not errors, errors
        print("PASS selected/due reviews, skip, history, thin-bar flight, card unfurl, mobile and reduced motion")


if __name__ == "__main__":
    run()
