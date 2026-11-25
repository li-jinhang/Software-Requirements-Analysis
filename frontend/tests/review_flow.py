"""Exercise independent recall and targeted Feynman assessment with fixture inference."""
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile

from playwright.sync_api import expect, sync_playwright
from live_lookup import ROOT, port, serve
from online_sync import login, start_demo_practice


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
        login(page, "student", "123456")
        start_demo_practice(page)
        button("开始教学").click()
        rubric = page.get_by_role("button", name="Agent 认知文档", exact=False)
        rubric.click()
        button("考核此条").first.click()
        expect(page.get_by_text("正在考核选定条目", exact=True)).to_be_visible()
        page.get_by_label("收起百科").click()
        page.get_by_label("讲解内容").fill("梯度变大")
        button("发送讲解").click()
        expect(page.locator(".p1 .correction-card")).to_be_visible()
        completed = page.get_by_text("本轮定向考核已完成", exact=True)
        expect(completed).to_be_visible()
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
        expect(completed).to_be_visible()
        assert page.evaluate("window.flights.length") == 0

        button("复习").click()
        expect(page.get_by_role("heading", name="还没有可复习的知识点", exact=True)).to_be_visible()
        button("费曼学习").click()
        page.set_viewport_size({"width": 390, "height": 844})
        rubric.click()
        button("考核此条").first.click()
        page.get_by_label("讲解内容").fill("梯度变大")
        button("发送讲解").click()
        expect(page.locator(".p1 .correction-card")).to_be_visible()
        expect(completed).to_be_visible()
        assert page.evaluate("window.flights.length") == 0
        page.set_viewport_size({"width": 1440, "height": 980})
        page.emulate_media(reduced_motion="reduce")
        rubric.click()
        button("考核此条").first.click()
        expect(page.get_by_text("正在考核选定条目", exact=True)).to_be_visible()
        page.get_by_label("讲解内容").fill("梯度变大")
        button("发送讲解").click()
        expect(page.locator(".p1 .correction-card")).to_be_visible()
        expect(completed).to_be_visible()
        assert page.evaluate("window.flights.length") == 0

        api_base = api + "/api/v1"
        auth = page.request.post(api_base + "/auth/login", data={"username": "student", "password": "123456"})
        headers = {"Authorization": "Bearer " + auth.json()["access_token"]}
        doc = page.request.get(api_base + "/documents", headers=headers).json()[0]["id"]
        document = f"{api_base}/documents/{doc}"
        rubric_data = page.request.get(document + "/cognitive-doc", headers=headers).json()
        items = rubric_data["sections"][0]["items"]
        for item in items:
            rubric.click()
            with page.expect_response(lambda r: r.url.endswith("/sessions") and r.request.method == "POST") as creation:
                page.locator(f'[data-item-id="{item["id"]}"]').get_by_role("button", name="考核此条").click()
            assert creation.value.json()["mode"] == "assessment"
            page.get_by_label("讲解内容").fill(item["statement"])
            button("发送讲解").click()
            expect(completed).to_be_visible()

        before = page.request.get(document + "/due-reviews", headers=headers).json()
        cards = before["all_reviewable_items"]
        assert len(cards) == len(items) >= 2
        assert not before["due_items"] and all(c["mastery_score"] == 100 and c["review_count"] == 0 for c in cards)
        saved_sessions = page.request.get(api_base + "/sessions", headers=headers).json()
        graph_before = page.request.get(document + "/graph", headers=headers).json()
        button("复习").click()
        button("查看全部复习卡片").click()
        expect(page.locator(".review-answer")).to_have_count(0)
        button("查看答案").click()
        expect(page.locator(".review-answer")).to_contain_text(cards[0]["statement"])
        button("记得").click()
        expect(page.get_by_text("已记录，复习计划已更新。", exact=True)).to_be_visible()
        button("查看答案").click()
        button("需巩固").click()
        expect(page.get_by_text("已安排 10 分钟后再复习。", exact=True)).to_be_visible()
        assert page.request.get(api_base + "/sessions", headers=headers).json() == saved_sessions
        assert page.request.get(document + "/graph", headers=headers).json() == graph_before
        after = page.request.get(document + "/due-reviews", headers=headers).json()["all_reviewable_items"]
        recorded = [item for item in after if item["review_count"] == 1]
        assert len(recorded) == 2 and {item["review_stage"] for item in recorded} == {0, 1}
        page.request.post(document + "/simulate-time-advance", data={"days": 2}, headers=headers)
        page.reload()
        expect(page.get_by_text("已到期", exact=True)).to_be_visible()
        button("明天再复习").click()
        expect(page.get_by_text("已延到明天。", exact=True)).to_be_visible()
        assert not errors, errors
        print("PASS standalone recall, spacing, independent mastery, assessment, correction flight, mobile and reduced motion")


if __name__ == "__main__":
    run()
