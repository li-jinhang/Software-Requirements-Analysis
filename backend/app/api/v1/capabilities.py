import json
from io import BytesIO
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func

from app.api.v1.documents import owned
from app.api.v1.sessions import session_guard
from app.core.security import current_user, require_local_workbench
from app.db.database import get_db
from app.db.database import SessionLocal
from app.db.models import Document, Session, Message, public
from app.services import extension_store as storage, extension_mcp, memory_service as memory

router = APIRouter(prefix="/capabilities", tags=["命令与能力"])


@router.get("")
def catalog(user=Depends(current_user)):
    return {"skills": storage.skills(user.id), "servers": storage.servers(user.id)}


@router.get("/skills/template")
def template(user=Depends(current_user)):
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        z.writestr("study-review/SKILL.md", "---\nname: study-review\ndescription: 根据本次课程整理薄弱点、复习提纲与自测题。\nmetadata:\n  version: '1.0'\n---\n根据用户任务和课程依据，输出简明复习笔记、三个自测问题及答案。缺少依据时说明。参考 references/format.md 的格式。")
        z.writestr("study-review/references/format.md", "# 复习笔记\n## 核心概念\n## 容易混淆的条件\n## 三道自测题\n## 参考答案\n")
    return Response(out.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="study-review.zip"'})


@router.post("/skills/import", status_code=201)
async def import_skill(file: UploadFile = File(...), replace: bool = Form(False), user=Depends(current_user)):
    data = await file.read(10 * 1024**2 + 1)
    return storage.import_skill(data, user.id, replace)


@router.get("/skills/{id}")
def skill_detail(id: str, user=Depends(current_user)):
    row = storage.skill(id, user.id)
    return storage.skill_out(row, user.id) | {"instructions": row["body"], "metadata": json.loads(row["metadata"])}


class SkillChange(BaseModel):
    enabled: bool | None = None
    shared: bool | None = None


@router.patch("/skills/{id}", status_code=204)
def change_skill(id: str, body: SkillChange, user=Depends(current_user)):
    row = storage.skill(id, user.id)
    if row["owner"] != user.id:
        raise HTTPException(403, "只能管理自己的技能")
    if body.shared is not None and user.role != "admin":
        raise HTTPException(403, "只有管理员可以发布公共技能")
    with storage.store() as db:
        db.execute("UPDATE skills SET enabled=?,shared=? WHERE id=? AND owner=?", (
            row["enabled"] if body.enabled is None else body.enabled,
            row["shared"] if body.shared is None else body.shared, id, user.id))


@router.delete("/skills/{id}", status_code=204)
def remove_skill(id: str, user=Depends(current_user)):
    with storage.store() as db:
        if not db.execute("DELETE FROM skills WHERE id=? AND owner=?", (id, user.id)).rowcount:
            raise HTTPException(404, "技能不存在或不属于当前账号")


class Connection(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2048)
    token: str | None = Field(default=None, max_length=4096)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def address(cls, value):
        return extension_mcp.validate_url(value)


def local_endpoint_access(body, request):
    # Connection management runs on the service host, rather than exposing
    # arbitrary server-side HTTP probes to remote student accounts.
    require_local_workbench(request)


@router.post("/servers", status_code=201)
def add_server(body: Connection, request: Request, user=Depends(current_user)):
    local_endpoint_access(body, request)
    id = uuid4().hex
    with storage.store() as db:
        db.execute("INSERT INTO servers(id,owner,name,url,token,enabled) VALUES(?,?,?,?,?,?)",
                   (id, user.id, body.name, body.url, body.token or "", body.enabled))
    return storage.server_out(storage.server(id, user.id))


@router.put("/servers/{id}")
def edit_server(id: str, body: Connection, request: Request, user=Depends(current_user)):
    local_endpoint_access(body, request)
    old = storage.server(id, user.id)
    if old["url"] != body.url and body.token is None and old["token"]:
        raise HTTPException(422, "修改服务地址时请重新填写令牌，或明确清空令牌")
    key = old["token"] if body.token is None else body.token
    with storage.store() as db:
        db.execute("UPDATE servers SET name=?,url=?,token=?,enabled=?,tools='[]',status='未连接' WHERE id=? AND owner=?",
                   (body.name, body.url, key, body.enabled, id, user.id))
    return storage.server_out(storage.server(id, user.id))


@router.delete("/servers/{id}", status_code=204)
def remove_server(id: str, user=Depends(current_user)):
    with storage.store() as db:
        if not db.execute("DELETE FROM servers WHERE id=? AND owner=?", (id, user.id)).rowcount:
            raise HTTPException(404, "MCP 连接不存在")


@router.post("/servers/{id}/connect")
async def connect(id: str, request: Request, user=Depends(current_user)):
    require_local_workbench(request)
    await extension_mcp.request(storage.server(id, user.id))
    return storage.server_out(storage.server(id, user.id))


class Execution(BaseModel):
    session_id: int
    window: Literal["feynman", "encyclopedia"] = "feynman"
    kind: Literal["skill", "mcp", "progress", "history", "lookup"]
    capability_id: str = ""
    content: str = Field(default="", max_length=16000)
    tool: str = Field(default="", max_length=256)
    arguments: dict = Field(default_factory=dict)
    confirmed: bool = False
    request_id: str = Field(min_length=8, max_length=80)


@router.get("/runs")
def runs(session_id: int, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Session, session_id, user)
    with storage.store() as store:
        return [dict(r) for r in store.execute("SELECT id,kind,capability_id,status,created_at FROM runs WHERE owner=? AND session_id=? ORDER BY created_at DESC LIMIT 30", (user.id, session_id))]


@router.post("/execute")
def execute(body: Execution, request: Request, db=Depends(get_db), user=Depends(current_user)):
    from app.agents.capability_graph import capability_graph
    with session_guard(body.session_id):
        session = owned(db, Session, body.session_id, user)
        if session.status != "active":
            raise HTTPException(409, "会话已结束")
        # Store the durable messages first, audit status second; replay looks up
        # the message key too, so a late audit write cannot repeat an external call.
        old = db.scalar(select(Message).where(Message.session_id == session.id,
            Message.sender_role == "assistant", Message.meta_json.contains(body.request_id)))
        if old and json.loads(old.meta_json).get("capability_run") == body.request_id:
            return {"message": public(old), "replayed": True}
        with storage.store() as store:
            old_run = store.execute("SELECT owner,status FROM runs WHERE id=?", (body.request_id,)).fetchone()
            if old_run:
                raise HTTPException(409, "本次操作已经提交或中断，请核对运行记录，勿重复执行")
        doc = owned(db, Document, session.doc_id, user)
        context = memory.get_memory_context(db, doc.id, session.id)
        state = {"kind": body.kind, "content": body.content, "context": {
            "课程": doc.title, "节点": context["nodes"][:30], "薄弱点": context["weaknesses"][-20:],
            "原文": memory.search_rag(doc.id, body.content) if body.kind in {"skill", "lookup"} else [],
        }}
        version = ""
        if body.kind == "skill":
            row = storage.skill(body.capability_id, user.id)
            if not row["enabled"] or row["reason"]:
                raise HTTPException(409, row["reason"] or "技能已停用")
            if not body.content.strip():
                raise HTTPException(422, "请先输入要交给技能处理的任务")
            version = row["version"]
            state["instructions"] = row["body"][:32000] + "\n配套文本资料：\n" + json.dumps(json.loads(row["files"]), ensure_ascii=False)[:22000]
        if body.kind == "mcp":
            require_local_workbench(request)
            if not body.confirmed:
                raise HTTPException(422, "请核对工具和参数后确认执行")
            state.update(server=storage.server(body.capability_id, user.id), tool=body.tool, arguments=body.arguments)
        history = [public(m) for m in db.scalars(select(Message).where(Message.session_id == session.id,
            func.json_extract(Message.meta_json, "$.no_assessment").is_(None)).order_by(Message.id.desc()).limit(12))]
        session_id, owner = session.id, user.id
        db.rollback()  # No SQLite read snapshot/write lock during remote inference.
        with storage.store() as store:
            store.execute("INSERT INTO runs(id,owner,session_id,kind,capability_id,input,status,metadata) VALUES(?,?,?,?,?,?,?,?)",
                (body.request_id, owner, session_id, body.kind, body.capability_id, body.content, "running", json.dumps({"tool": body.tool, "version": version})))
        try:
            if body.kind in {"skill", "mcp"}:
                result = capability_graph.invoke(state)
                text = result["output"]
            elif body.kind == "progress":
                nodes = context["nodes"]
                text = "### 学习进度\n" + "\n".join(f"- {n['concept_name']}：{n['mastery_score']} 分（{n['status']}）" for n in nodes)
                text += "\n\n### 薄弱点\n" + ("\n".join("- " + w["detail"] for w in context["weaknesses"]) or "暂无未解决记录。")
            elif body.kind == "lookup":
                text = "### 课件检索\n" + ("\n\n".join(f"**{h['doc_title']} · 片段 #{h['chunk_index']}**\n\n{h['snippet']}" for h in state["context"]["原文"]) or "当前资料没有匹配原文。")
            else:
                text = "### 最近会话记录\n" + "\n\n".join(f"**{m['sender_role']} · {m['window_type']}**\n\n{m['content'][:1500]}" for m in reversed(history))
            meta = {"capability_run": body.request_id, "capability_kind": body.kind, "skill_version": version,
                    "capability_id": body.capability_id, "tool": body.tool, "no_assessment": True}
            if body.kind == "lookup" and context["nodes"]:
                node = max(context["nodes"], key=lambda n: (n["concept_name"] in body.content, len(set(n["concept_name"]) & set(body.content))))
                memory.log_weakness(db, session_id, node["id"], "need_lookup", body.content)
            db.add(Message(session_id=session_id, window_type=body.window, sender_role="user", content=body.content or "/" + body.kind, meta_json=json.dumps(meta)))
            msg = Message(session_id=session_id, window_type=body.window, sender_role="assistant", content=text[:100000], meta_json=json.dumps(meta))
            db.add(msg)
            db.commit()
            with storage.store() as store:
                store.execute("UPDATE runs SET status='completed',output=? WHERE id=?", (text[:100000], body.request_id))
            return {"message": public(msg), "replayed": False}
        except Exception:
            db.rollback()
            with storage.store() as store:
                store.execute("UPDATE runs SET status='failed-or-uncertain' WHERE id=?", (body.request_id,))
            raise


@router.post("/execute/stream")
def execute_stream(body: Execution, request: Request, db=Depends(get_db), user=Depends(current_user)):
    from queue import SimpleQueue
    from types import SimpleNamespace
    from app.api.v1.sessions import workers
    from app.agents.capability_graph import token_sink
    owned(db, Session, body.session_id, user)
    owner, role = user.id, user.role
    events = SimpleQueue()
    def frame(kind, value):
        return "event: " + kind + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"
    def work():
        visible = False
        def emit(delta):
            nonlocal visible
            visible = True
            events.put(frame("token", {"delta": delta}))
        token = token_sink.set(emit)
        try:
            with SessionLocal() as worker_db:
                result = execute(body, request, worker_db, SimpleNamespace(id=owner, role=role))
                if not visible:
                    emit(result["message"]["content"])
                events.put(frame("done", {"message_id": result["message"]["id"]}))
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "能力执行中断，请核对历史和目标服务后再重试"
            events.put(frame("error", {"detail": detail}))
        finally:
            token_sink.reset(token)
            events.put(None)
    workers.submit(work)
    def frames():
        yield frame("status", {"text": "正在调用能力"})
        while (item := events.get()) is not None:
            yield item
    return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
