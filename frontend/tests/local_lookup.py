"""Regression for revisiting different left-pane turns about the same concept."""
import os

from playwright.sync_api import expect, sync_playwright


def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge")
        page = browser.new_page()
        page.goto(os.getenv("FM_FRONTEND", "http://127.0.0.1:5173"))
        button = lambda name: page.get_by_role("button", name=name, exact=True)
        button("开讲").first.click()
        button("开始教学").click()
        questions = ["梯度下降的更新公式是什么？", "梯度下降时学习率过大会怎样？"]
        for question in questions:
            page.get_by_label("讲解内容").fill(question)
            button("发送讲解").click()
        # Click the earlier turn after the later one already exists.
        for index, (question, expected) in enumerate(zip(questions, ["更新规则", "震荡"]), 1):
            button("去右侧查证").nth(index).click()
            feed = page.locator(".p1")
            expect(feed.locator(".mine").last).to_contain_text(question)
            reply = feed.locator("article:not(.mine)").last
            expect(reply.locator("p").first).to_contain_text(expected)
            reply.locator("summary").click()
            expect(reply.locator("details[open]")).to_contain_text(expected)
        browser.close()


if __name__ == "__main__":
    run()
