"""Headless UI regression with intercepted APIs; never touches personal models/data."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def run(browser):
    frontend = os.getenv("FM_FRONTEND", "http://127.0.0.1:5173")
    backend = os.getenv("FM_BACKEND", "http://127.0.0.1:18080")
    page = browser.new_page(viewport={"width":1280,"height":900})
    user_id = "api:" + backend + ":fixture"
    page.add_init_script("sessionStorage.setItem('fm.user'," + json.dumps(json.dumps({"id":user_id,"name":"界面验收","admin":False})) + ");sessionStorage.setItem(" + json.dumps("fm.jwt:"+backend) + ",'fixture');sessionStorage.setItem(" + json.dumps("fm.active:"+user_id) + ",'1');")
    skills=[{"id":"s1","name":"study-review","description":"复习提纲与自测题","version":"1.0","enabled":True,"shared":False,"editable":True,"available":True,"reason":"","files":[]}]
    servers=[{"id":"m1","name":"学习档案","url":"http://127.0.0.1:18090/mcp","enabled":True,"has_token":False,"status":"已连接","tools":[{"name":"archive_note","description":"归档笔记","inputSchema":{"type":"object","properties":{"title":{"type":"string"}},"required":["title"]},"annotations":{}}]}]
    messages=[{"id":1,"window_type":"feynman","sender_role":"assistant","content":"请解释梯度。","meta_json":{}}]
    calls=[]
    def route(r):
        path=r.request.url.split("/api/v1",1)[1].split("?",1)[0]
        data={}
        if path=="/auth/me": data={"username":"fixture"}
        elif path=="/documents": data=[{"id":1,"title":"验收课程","subject":"数学","file_type":"md","parse_status":"completed"}]
        elif path.endswith("/memory"): data={"nodes":[{"id":1,"concept_name":"梯度","summary":"局部上升方向","importance":3,"mastery_score":0,"status":"unlearned","key_points":[]}],"edges":[],"weaknesses":[]}
        elif path=="/sessions": data=[{"id":1,"doc_id":1,"persona_type":"peer","status":"active","target_node_id":1}]
        elif path.endswith("/messages"): data=messages
        elif path.endswith("/cognitive-doc"): data={"doc_id":1,"total":1,"mastered_count":0,"mastery_score":0,"sections":[],"current_item_id":None}
        elif path.endswith("/due-reviews"): data={"due_items":[],"all_reviewable_items":[]}
        elif path=="/model-settings/availability": data={k:True for k in ("extract","feynman","encyclopedia","report")}
        elif path=="/capabilities": data={"skills":skills,"servers":servers}
        elif path=="/capabilities/execute/stream":
            body=r.request.post_data_json;calls.append(body)
            content="技能生成的复习笔记" if body["kind"]=="skill" else "工具归档完成"
            messages.extend([{"id":len(messages)+1,"window_type":body["window"],"sender_role":"user","content":body["content"],"meta_json":{"no_assessment":True}},
                             {"id":len(messages)+2,"window_type":body["window"],"sender_role":"assistant","content":content,"meta_json":{"no_assessment":True,"capability_kind":body["kind"],"skill_version":"1.0"}}])
            stream="event: token\ndata: "+json.dumps({"delta":content})+"\n\nevent: done\ndata: "+json.dumps({"message_id":messages[-1]["id"]})+"\n\n"
            r.fulfill(status=200,content_type="text/event-stream",body=stream);return
        else:
            raise AssertionError("Unexpected API: "+path)
        r.fulfill(status=200,content_type="application/json",body=json.dumps(data))
    page.route("**/api/v1/**",route)
    page.goto(frontend+"/#studio")
    field=page.get_by_label("讲解内容",exact=True)
    expect(field).to_be_editable()
    field.fill("/");expect(page.get_by_role("listbox")).to_be_visible()
    field.press("ArrowDown");expect(page.get_by_role("listbox").get_by_role("option").nth(1)).to_have_attribute("aria-selected","true")
    field.press("Escape");expect(page.get_by_role("listbox")).not_to_be_visible()
    field.fill("/study");page.get_by_role("listbox").get_by_role("option").filter(has_text="study-review").click()
    expect(page.locator(".selected-skill")).to_contain_text("study-review")
    field.fill("写一份复习笔记");field.press("Enter")
    expect(page.locator(".feed")).to_contain_text(["技能生成的复习笔记",""])
    assert calls[0]["kind"]=="skill" and calls[0]["capability_id"]=="s1"
    expect(page.locator(".selected-skill")).to_have_count(0)
    field.fill("/archive");page.get_by_role("listbox").get_by_role("option").filter(has_text="archive_note").click()
    modal=page.get_by_role("dialog",name="调用工具 · archive_note",exact=True)
    expect(modal).to_be_visible()
    modal.get_by_label("标题",exact=False).fill('复习记录')
    modal.get_by_role("button",name="确认执行",exact=True).click()
    expect(modal).not_to_be_visible()
    assert calls[-1]["kind"]=="mcp" and calls[-1]["arguments"]=={"title":"复习记录"} and calls[-1]["confirmed"]
    field.fill("/skills");field.press("Enter")
    expect(page.get_by_role("dialog",name="能力中心",exact=True)).to_be_visible()
    page.get_by_role("dialog",name="能力中心",exact=True).get_by_role("button",name="关闭",exact=True).click()
    field.fill("/")
    folder=Path(__file__).resolve().parents[2]/"capabilities-test.local"
    folder.mkdir(exist_ok=True)
    page.screenshot(path=str(folder/"desktop.png"))
    page.set_viewport_size({"width":390,"height":844})
    expect(page.get_by_role("listbox")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(folder/"mobile.png"))
    print("PASS slash keyboard/menu, skill selection + streaming, MCP arguments + confirmation, manager, desktop/mobile layout")
    page.close()


if __name__=="__main__":
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        try:run(browser)
        finally:browser.close()
