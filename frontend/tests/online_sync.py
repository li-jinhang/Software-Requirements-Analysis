"""Run against a disposable seeded dev.py instance. Requires Python Playwright and Edge.
Set FM_STUDENT_PASSWORD and FM_ADMIN_PASSWORD to the test instance credentials.
The run uploads one course and completes one teaching session.
"""
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def login(page, username):
    page.locator(".account button").click()
    page.get_by_label("用户名").fill(username)
    page.get_by_label("密码", exact=True).fill(os.environ[f"FM_{username.upper()}_PASSWORD"])
    page.get_by_role("button", name="登录", exact=True).click()
    expect(page.locator(".account")).to_contain_text("在线服务")


def button(page, name):
    return page.get_by_role("button", name=name, exact=True)


def run(browser):
    root = os.environ.get("FM_FRONTEND", "http://127.0.0.1:5173")
    page = browser.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(root)
    login(page, "student")
    expect(page.locator(".card").first).to_be_visible()
    sample = next((Path(__file__).resolve().parents[2] / "backend/data/sample_docs").glob("*.md"))
    title = "联调-" + uuid4().hex[:8]
    button(page, "上传资料").click()
    page.locator("input[type=file]").set_input_files(
        {"name": title + ".md", "mimeType": "text/markdown", "buffer": sample.read_bytes()}
    )
    with page.expect_response(lambda r: r.url.endswith("/documents/upload")) as upload:
        button(page, "解析资料").click()
    assert upload.value.status == 201
    drawer = page.get_by_role("dialog")
    drawer.locator("summary").first.click()
    concept = drawer.locator("summary").first.inner_text().split()[0]
    drawer.locator("details[open]").get_by_label("个人笔记").fill("联调笔记")
    with page.expect_response(lambda r: r.url.endswith("/note")) as note:
        drawer.get_by_role("button", name="同步笔记").click()
    assert note.value.json()["user_note"] == "联调笔记"
    for label, fmt in [("导出 Markdown", "md"), ("导出 JSON", "json")]:
        with page.expect_download() as event, page.expect_response(lambda r: r.url.endswith("export?format=" + fmt)) as export:
            button(drawer, label).click()
        assert export.value.ok and event.value.path().stat().st_size > 0
    drawer.get_by_label("关闭", exact=True).click()
    page.locator(".card").filter(has_text=title).get_by_role("button", name="开讲").click()
    with page.expect_response(lambda r: r.url.endswith("/sessions") and r.request.method == "POST") as created:
        button(page, "开始教学").click()
    assert created.value.status == 201
    session_id = created.value.json()["id"]
    with page.expect_response(lambda r: r.url.endswith("/persona")) as changed:
        page.get_by_label("听众角色").select_option("严苛考官")
    assert changed.value.json()["persona_type"] == "examiner"
    def truncate_done(route):
        response = route.fetch()
        body = response.body().split(b"event: done", 1)[0]
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    for turn in range(2):
        if turn == 1:
            page.route("**/feynman/stream", truncate_done, times=1)
        lecture = f"{concept}的适用条件与具体例子，第{turn + 1}轮复述。"
        page.get_by_label("讲解内容").fill(lecture)
        button(page, "发送讲解").click()
        expect(page.locator(".p0 .msg.mine")).to_have_count(turn + 1)
    expect(page.get_by_role("status")).to_contain_text("已从服务端恢复")
    expect(page.get_by_label("讲解内容")).to_have_value("")
    button(page, "去右侧查证").last.click()
    expect(page.locator(".p1 details")).to_have_count(1)
    page.get_by_label("讲解内容").fill("未发送的草稿")
    with page.expect_response(lambda r: r.url.endswith(f"/sessions/{session_id}/messages")) as restored:
        page.reload()
    history = restored.value.json()
    expect(page.get_by_label("讲解内容")).to_have_value("未发送的草稿")
    expect(page.get_by_label("听众角色")).to_have_value("严苛考官")
    answers = [m for m in history if m["sender_role"] == "assistant"]
    assert len(answers) == 3
    for message in answers:
        selector = ".p0" if message["window_type"] == "feynman" else ".p1"
        assert message["content"].strip()
        expect(page.locator(selector + " .feed")).to_contain_text(message["content"].splitlines()[0])
        for update in message["meta_json"].get("updates", []):
            expect(page.locator(selector + " .feed")).to_contain_text(update["tag"])
    citations = answers[-1]["meta_json"]["citations"]
    assert citations
    page.locator(".p1 summary").click()
    for source in citations:
        expect(page.locator(".p1 details")).to_contain_text(source["snippet"])
    updates = answers[-2]["meta_json"]["updates"]
    assert updates
    with page.expect_response(lambda r: "/documents/" in r.url and r.url.endswith("/memory")) as memory:
        button(page, "知识图谱").click()
    scores = {n["id"]: n["mastery_score"] for n in memory.value.json()["nodes"]}
    # The navigation entry uses the selected course. Confirm the session's graph explicitly.
    if updates[0]["node_id"] not in scores:
        with page.expect_response(lambda r: r.url.endswith("/memory")) as memory:
            page.get_by_label("图谱资料").select_option(str(upload.value.json()["id"]))
        scores = {n["id"]: n["mastery_score"] for n in memory.value.json()["nodes"]}
    assert all(scores[u["node_id"]] == u["mastery_score"] for u in updates)
    button(page, "双窗工作台").click()
    with page.expect_response(lambda r: r.url.endswith("/report") and r.request.method == "POST") as report:
        button(page, "结束并生成报告").click()
    assert report.value.json()["session_id"] == session_id
    expect(page.locator(".report")).to_contain_text(title)
    # Fresh browser storage proves that reports and conversations come from the API.
    fresh = browser.new_page()
    fresh.goto(root)
    login(fresh, "student")
    button(fresh, "历史报告").click()
    fresh.get_by_label("历史报告").select_option(str(report.value.json()["id"]))
    button(fresh, "查看双窗历史").click()
    expect(fresh.locator(".p1 details")).to_have_count(1)
    expect(button(fresh, "发送讲解")).to_be_disabled()

    login(page, "admin")
    for label, table, field in [
        ("日志", "weakness_logs", "detail"), ("报告", "diagnostic_reports", "overall_comment"),
        ("文档", "documents", "title"), ("节点", "knowledge_nodes", "concept_name"),
    ]:
        with page.expect_response(lambda r: "/admin/tables/" + table + "?" in r.url) as response:
            button(page, label).click()
        rows = response.value.json()["items"]
        assert rows
        expect(page.locator(".record")).to_have_count(len(rows))
        for index, row in enumerate(rows):
            record = page.locator(".record").nth(index)
            expect(record.locator("strong")).to_have_text(row[field] or f"会话 {row['session_id']}")
            expect(record.locator("small")).to_contain_text(row.get("updated_at") or row["created_at"])
    first = page.locator(".record code").all_text_contents()
    with page.expect_response(lambda r: "knowledge_nodes?" in r.url and "page=2" in r.url) as result:
        button(page, "下一页").click()
    assert result.value.json()["page"] == 2
    expect(page.locator(".record code").first).to_have_text(str(result.value.json()["items"][0]["id"]))
    assert set(first).isdisjoint(page.locator(".record code").all_text_contents())
    for label, fmt in [("导出全部 JSON","json"),("下载 SQLite","db")]:
        with page.expect_download() as event:
            button(page,label).click()
        path = event.value.path()
        if fmt == "json":
            data = json.loads(path.read_bytes())
            assert len(data) == 8 and all("password_hash" not in u for u in data["users"])
        else:
            with sqlite3.connect(path) as db:
                assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                assert db.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0] == len(history)
    assert not errors, errors
    print("PASS student, both streams, persisted messages/citations/mastery, report, fresh browser, notes, exports and admin pagination")


if __name__ == "__main__":
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.environ.get("FM_BROWSER", "msedge"))
        try:
            run(browser)
        finally:
            browser.close()
