"""Run against a disposable, seeded workbench with no configured model."""
import os

from playwright.sync_api import expect, sync_playwright
from online_sync import login


def run(browser):
    page = browser.new_page()
    root = os.getenv("FM_FRONTEND", "http://127.0.0.1:5173")
    api = os.getenv("FM_BACKEND", "http://127.0.0.1:8000") + "/api/v1/model-settings"
    page.goto(root)
    assert not any(page.request.get(api + "/availability").json().values())

    def button(name, owner=page):
        return owner.get_by_role("button", name=name, exact=True)

    prompt = page.get_by_role("dialog", name="请配置模型", exact=True)
    login(page, "student")
    for action in ("开讲", "上传资料", "输入知识点生成"):
        button(action).first.click()
        expect(prompt).to_be_visible()
        button("前往模型配置", prompt).click()
        settings = page.get_by_role("dialog", name="设置", exact=True)
        expect(button("新建配置", settings)).to_be_visible()
        button("关闭", settings).click()

    # Open a session with a binding, then clear it before attempting any inference.
    profile_id = page.request.post(api + "/profiles", data={
        "name": "模型配置回归", "mode": "api", "model": "fixture",
        "base_url": "http://127.0.0.1:1/v1",
    }).json()["id"]
    try:
        button("开讲").first.click()
        button("开始教学").click()
        left = page.get_by_label("讲解内容")
        expect(left).to_be_visible()
        assert page.request.put(api + "/bindings/default", data={"profile_id": None}).ok
        left.fill("保留这段未发送的讲解")
        button("发送讲解").click()
        expect(prompt).to_be_visible()
        button("关闭", prompt).click()
        expect(left).to_have_value("保留这段未发送的讲解")
        right = page.get_by_label("百科问题")
        right.fill("为什么需要学习率？")
        button("提交查证").click()
        expect(prompt).to_be_visible()
        button("关闭", prompt).click()
        expect(right).to_have_value("为什么需要学习率？")
        button("结束并生成报告").click()
        expect(prompt).to_be_visible()
        expect(page.locator(".feed .msg")).to_have_count(1)  # Only the previously accepted opening.
    finally:
        current = page.request.get(api).json()["bindings"].get("default")
        if current == profile_id:
            assert page.request.put(api + "/bindings/default", data={"profile_id": None}).ok
        assert page.request.delete(api + "/profiles/" + profile_id).ok
    print("PASS no-model prompts on upload, session creation, both streams and report")


if __name__ == "__main__":
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge")
        try:
            run(browser)
        finally:
            browser.close()
