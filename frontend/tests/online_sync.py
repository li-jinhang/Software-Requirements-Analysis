"""Run against a disposable seeded backend with a configured test model. Requires Python Playwright and Edge.
Set FM_STUDENT_PASSWORD and FM_ADMIN_PASSWORD to the test instance credentials.
The run uploads one course and completes one teaching session.
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def submit_login(dialog, username):
    dialog.get_by_label("用户名").fill(username)
    dialog.get_by_label("密码", exact=True).fill(os.environ[f"FM_{username.upper()}_PASSWORD"])
    button(dialog, "登录").click()


def login(page, username):
    page.locator(".account button").click()
    submit_login(page.get_by_role("dialog"), username)
    expect(page.locator(".account")).to_contain_text("在线服务")


def button(page, name):
    return page.get_by_role("button", name=name, exact=True)


def check_local_model_settings(browser):
    root = os.environ.get("FM_FRONTEND", "http://127.0.0.1:5173")
    profile_id = uuid4().hex
    archive = {
        "version": 1,
        "profiles": [{
            "id": profile_id,
            "name": "迁移验证-" + profile_id[:8],
            "mode": "chatgpt",
            "model": "fixture-model",
            "base_url": "",
        }],
        "bindings": {},
    }
    for online in (False, True):
        page = browser.new_page()
        try:
            page.goto(root)
            if online:
                login(page, "student")
            button(page, "设置").click()
            settings = page.get_by_role("dialog", name="设置", exact=True)
            button(settings, "新建配置").click()
            editor = page.get_by_role("dialog", name="新建配置", exact=True)
            expect(editor).to_be_visible()
            button(editor, "关闭").click()
            with page.expect_file_chooser() as chooser:
                button(settings, "导入配置").click()
            chooser.value.set_files({
                "name": "models.json", "mimeType": "application/json",
                "buffer": json.dumps(archive, ensure_ascii=False).encode("utf-8"),
            })
            expect(settings.get_by_role("status")).to_contain_text("已导入 1 个配置")
            with page.expect_download() as download:
                button(settings, "导出配置").click()
            backup = json.loads(download.value.path().read_bytes())
            assert archive["profiles"][0] in backup["profiles"]
            card = settings.get_by_role("article").filter(has_text=archive["profiles"][0]["name"])
            button(card, "删除").click()
            button(page.get_by_role("dialog", name="删除模型配置"), "确认删除").click()
            expect(card).to_have_count(0)
        finally:
            page.close()
    print("PASS local model settings import and export from demo and student sessions")


def check_graph_course_switch(browser):
    root = os.environ.get("FM_FRONTEND", "http://127.0.0.1:5173")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(root + "/#graph")
        nodes = page.locator(".react-flow__node")
        search = page.get_by_label("搜索知识点")
        expect(nodes.first).to_be_visible()
        search.fill("学习率")
        expect(nodes).to_have_count(1)
        page.get_by_label("图谱资料").select_option(index=1)
        expect(search).to_have_value("")
        expect(page.locator(".react-flow__edge").first).to_be_visible()
        search.fill("没有这个知识点")
        expect(page.get_by_role("status")).to_have_text("没有匹配的知识点。")
        button(page, "清除搜索").click()
        expect(nodes.first).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("""() => {
            const canvas = document.querySelector('.react-flow').getBoundingClientRect();
            const bounds = [...document.querySelectorAll('.react-flow__node')].map(n => n.getBoundingClientRect());
            return bounds.length > 0 && bounds.every(b => b.left >= canvas.left - 1
                && b.right <= canvas.right + 1 && b.top >= canvas.top - 1 && b.bottom <= canvas.bottom + 1);
        }""")
    finally:
        page.close()
    print("PASS graph search, course switch, clear search and resize")


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
    # Look up an actual lesson section. The document title alone can legitimately have no hits.
    section = next(line[3:].strip() for line in sample.read_text(encoding="utf-8").splitlines()
                   if line.startswith("## "))
    expect(drawer.locator("summary").first).to_be_visible()
    matching = drawer.locator("summary").filter(has_text=section)
    entry = matching.first if matching.count() else drawer.locator("summary").first
    entry.click()
    concept = entry.inner_text().split()[0]
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
    # Wait for initial history and the workbench selector, not the setup dialog selector.
    role_selector = page.locator(".p0").get_by_label("听众角色")
    expect(role_selector).to_be_enabled()
    with page.expect_response(lambda r: r.url.endswith("/persona")) as changed:
        role_selector.select_option("严苛考官")
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
    assert len(answers) == 4  # opening, two teaching replies and one manual lookup
    for message in answers:
        selector = ".p0" if message["window_type"] == "feynman" else ".p1"
        assert message["content"].strip()
        expect(page.locator(selector + " .feed")).to_contain_text(message["content"].splitlines()[0].strip("*"))
        for update in message["meta_json"].get("updates", []):
            expect(page.locator(selector + " .feed")).to_contain_text(update["concept_name"] + " · " + update["tag"])
    citations = answers[-1]["meta_json"]["citations"]
    assert citations
    page.locator(".p1 summary").click()
    cards = page.locator(".p1 .source-card")
    expect(cards).to_have_count(len(citations))
    for card, source in zip(cards.all(), citations):
        expect(card.locator("header")).to_contain_text(f"{source['doc_title']} · 片段 #{source['chunk_index']}")
        more = card.get_by_role("button", name="展开全文")
        if more.count():
            more.click()
        # These Markdown course fixtures contain headings and prose.
        for line in source["snippet"].splitlines():
            if line.strip():
                expect(card).to_contain_text(line.lstrip("# "))
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
    expect(page.locator(".react-flow__edge-text").first).to_have_text(re.compile("^(前置依赖|包含|对比|应用于)$"))
    page.wait_for_function("""() => {
        const graph = document.querySelector('.react-flow')?.getBoundingClientRect();
        const nodes = [...document.querySelectorAll('.react-flow__node')];
        return graph && nodes.length && nodes.every(node => {
            const r = node.getBoundingClientRect();
            return r.width > 0 && r.top >= graph.top && r.bottom <= graph.bottom
                && r.left >= graph.left && r.right <= graph.right;
        });
    }""")
    button(page, "双窗工作台").click()
    with page.expect_response(lambda r: r.url.endswith("/report") and r.request.method == "POST") as report:
        button(page, "结束并生成报告").click()
    assert report.value.json()["session_id"] == session_id
    expect(page.locator(".report")).to_contain_text(title)
    expect(page.locator(".review-plan li").first).to_be_visible()
    expect(page.locator(".report")).not_to_contain_text("基线练习指标")
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
            assert len(data) == 9 and data["cognitive_items"] and all("password_hash" not in u for u in data["users"])
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
            check_graph_course_switch(browser)
            check_local_model_settings(browser)
            run(browser)
        finally:
            browser.close()
