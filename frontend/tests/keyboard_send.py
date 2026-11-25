"""Exercise both composers against a disposable, seeded workbench with a test model."""

import os

from playwright.sync_api import expect, sync_playwright
from online_sync import login, start_demo_practice


def run(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(os.getenv("FM_FRONTEND", "http://127.0.0.1:5173"))

        def button(name):
            return page.get_by_role("button", name=name, exact=True)

        login(page, "student")
        start_demo_practice(page)
        button("开始教学").click()

        for side, window, label, action, text in [
            (".p0", "feynman", "讲解内容", "发送讲解", "损失函数可以衡量预测误差"),
            (".p1", "encyclopedia", "百科问题", "提交查证", "均方误差怎么算？"),
        ]:
            field = page.get_by_label(label)
            sent = page.locator(side + " .msg.mine")
            field.fill(text)
            expect(button(action)).to_be_enabled()
            field.press("End")
            field.press("Shift+Enter")
            expect(field).to_have_value(text + "\n")
            field.dispatch_event("keydown", {"key": "Enter", "isComposing": True})
            field.dispatch_event("keydown", {"key": "Enter", "keyCode": 229})
            expect(sent).to_have_count(0)

            with page.expect_request(
                lambda request: request.url.endswith(f"/{window}/stream"), timeout=3000,
            ) as request:
                field.press("Enter")
            assert request.value.post_data_json == {"content": text}
            expect(sent).to_have_count(1)
            expect(sent).to_contain_text(text)
            expect(field).to_have_value("")
            expect(field).to_be_editable()
        print("PASS Enter submits both composers, Shift+Enter and IME confirmation stay in the input")
    finally:
        page.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("FM_BROWSER", "msedge"))
        try:
            run(browser)
        finally:
            browser.close()
