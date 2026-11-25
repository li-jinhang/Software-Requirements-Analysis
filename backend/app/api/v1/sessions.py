from app.schemas.responses import SessionOut, MessageOut, ReportOut
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from app.core.security import current_user
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue
from threading import Lock, RLock
from weakref import WeakValueDictionary
from app.db.database import get_db, SessionLocal

workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feynman-turn")
from app.db.models import Session, Document, KnowledgeNode, Message, DiagnosticReport, now, public
from app.api.v1.documents import owned
from app.schemas import SessionCreate, PersonaChange, Turn
from app.agents import feynman_agent, model_teaching
from app.services import memory_service
from app.services.model_profiles import ModelError, ModelRequired, require_profile
from app.agents.contracts import AgentInput, MemoryUpdate, Citation, Token, Done, TurnMetadata, Preview, EVENT_NAMES
from app.agents.contracts import CognitiveUpdate, EncyclopediaPush
from app.agents.teaching.model import TeachingModelError

router = APIRouter(prefix="/sessions", tags=["会话"])
_guards = WeakValueDictionary()
_guards_lock = Lock()


def session_guard(id):
    """Single-worker deployment: serialize a session without locking other users."""
    with _guards_lock:
        guard = _guards.get(id)
        if guard is None:
            guard = RLock()
            _guards[id] = guard
        return guard


def lock_active(db, id):
    if not db.execute(
        update(Session).where(Session.id == id, Session.status == "active").values(status="active")
    ).rowcount:
        raise HTTPException(409, "会话已结束")


@router.post("", status_code=201, response_model=SessionOut)
def create(body: SessionCreate, db=Depends(get_db), user=Depends(current_user)):
    if owned(db, Document, body.doc_id, user).parse_status != "completed":
        raise HTTPException(409, "资料尚未解析完成")
    target = body.node_id
    if target is not None:
        node = db.get(KnowledgeNode, target)
        if node is None or node.doc_id != body.doc_id:
            raise HTTPException(422, "目标知识点不属于当前资料")
        if body.mode == "guided" and node.mastery_score >= 80:
            raise HTTPException(409, "该知识点已掌握，请选择其他知识点。")
    elif body.mode == "guided":
        step = next((step for step in memory_service.compute_learning_path(db, body.doc_id)
                     if step["mastery_score"] < 80), None)
        if step is None:
            raise HTTPException(409, "当前资料没有尚未掌握的知识点。")
        target = step["node_id"]
    require_profile("encyclopedia" if body.mode == "guided" else "feynman")
    from app.services import cognitive
    ids = list(dict.fromkeys(body.target_item_ids))
    mode = "assessment" if body.mode == "review" else body.mode
    if mode == "assessment":
        if not ids:
            raise HTTPException(422, "请指定至少一个费曼考核条目")
        if not set(ids) <= {i["id"] for i in cognitive.items(db, body.doc_id)}:
            raise HTTPException(422, "考核条目不属于当前资料")
    elif ids:
        raise HTTPException(422, "指定条目需要使用定向考核模式")
    row = Session(user_id=user.id, target_node_id=target, target_item_ids=ids, mode=mode,
                  **body.model_dump(exclude={"node_id", "target_item_ids", "mode"}))
    db.add(row)
    db.commit()
    return public(row)


@router.get("", response_model=list[SessionOut])
def sessions(db=Depends(get_db), user=Depends(current_user)):
    return [
        public(s)
        for s in db.scalars(
            select(Session).where(Session.user_id == user.id).order_by(Session.id.desc())
        )
    ]


@router.patch("/{id}/persona", response_model=SessionOut)
def persona(id: int, body: PersonaChange, db=Depends(get_db), user=Depends(current_user)):
    with session_guard(id):
        s = owned(db, Session, id, user)
        lock_active(db, id)
        if s.persona_type != body.persona_type:
            labels = {"child": "启蒙小学生", "peer": "互助同窗", "examiner": "严苛考官"}
            s.persona_type = body.persona_type
            db.add(Message(session_id=id, window_type="feynman", sender_role="system",
                           content="听众已切换为" + labels[body.persona_type],
                           meta_json=json.dumps({"persona_type": body.persona_type})))
        db.commit()
        return public(s)


@router.get("/{id}/messages", response_model=list[MessageOut])
def history(id: int, db=Depends(get_db), user=Depends(current_user)):
    with session_guard(id):
        owned(db, Session, id, user)
        return [
            public(m)
            for m in db.scalars(select(Message).where(Message.session_id == id).order_by(Message.id))
        ]


def frame(event):
    return f"event: {EVENT_NAMES[type(event)]}\ndata: {event.model_dump_json()}\n\n"


def produce(id, content, window, user_id, events, action="explain"):
    """Finish the accepted turn even if the HTTP client disconnects."""
    try:
        from contextlib import ExitStack
        with session_guard(id), SessionLocal() as db, ExitStack() as guards:
            s = db.get(Session, id)
            if s is None or s.user_id != user_id:
                raise HTTPException(404, "会话不存在")
            if s.status != "active":
                raise HTTPException(409, "会话已结束")
            if window == "feynman":
                # Different sessions may teach the same document-wide rubric.
                guards.enter_context(session_guard(("document", s.doc_id)))
            if s.review_completed and window == "feynman" and action != "opening":
                raise HTTPException(409, "本轮定向考核已完成")
            if action == "lesson" and s.mode != "guided":
                raise HTTPException(409, "请从知识图谱或资料库开始 AI 讲解会话。")
            if action in ("opening", "lesson"):
                old = db.scalar(select(Message).where(
                    Message.session_id == id, Message.sender_role == "assistant",
                    Message.window_type == window,
                    func.json_extract(Message.meta_json, "$.input_kind") == action,
                ).order_by(Message.id).limit(1))
                if old:
                    if action == "lesson":
                        citations = json.loads(old.meta_json).get("citations", [])
                        events.put(frame(Citation(citations=citations)))
                    events.put(frame(Token(delta=old.content)))
                    events.put(frame(Done(message_id=old.id)))
                    return
            turn = AgentInput(
                session_id=id, doc_id=s.doc_id, persona_type=s.persona_type, content=content,
                target_node_id=s.target_node_id, action=action,
            )
            def emit_preview(text):
                events.put(frame(Preview(text=text)))

            if action == "lesson":
                stream = model_teaching.guided_lesson(db, turn, require_profile(window), emit_preview)
            elif window == "feynman":
                require_profile(window)
                stream = feynman_agent.stream(db, turn)
            else:
                prepared = model_teaching.prepare(
                    db, turn, window, require_profile(window),
                    emit_preview=emit_preview,
                )
                stream = model_teaching.apply(db, turn, prepared)
            text = []
            meta = {}
            node_ids = []
            pending_updates = []
            accepted_early = False
            for event in stream:
                if isinstance(event, EncyclopediaPush):
                    accepted_early = True
                if isinstance(event, CognitiveUpdate) and not accepted_early:
                    pending_updates.append(event)
                    continue
                if isinstance(event, TurnMetadata):
                    details = dict(event.metadata)
                    node_ids.extend(details.pop("related_node_ids", []))
                    meta.update(details)
                    continue
                if isinstance(event, Token):
                    text.append(event.delta)
                elif isinstance(event, Citation):
                    meta["citations"] = event.model_dump()["citations"]
                elif isinstance(event, MemoryUpdate):
                    meta["updates"] = event.model_dump()["updates"]
                    node_ids.extend(n.node_id for n in event.updates)
                    if not accepted_early:
                        pending_updates.append(event)
                        continue
                events.put(frame(event))
            lock_active(db, id)
            if action == "explain" and not accepted_early:
                db.add(Message(session_id=id, window_type=window, sender_role="user", content=content))
            msg = Message(
                session_id=id,
                window_type=window,
                sender_role="assistant",
                content="".join(text),
                related_node_ids=json.dumps(list(dict.fromkeys(node_ids))),
                meta_json=json.dumps(meta, ensure_ascii=False),
            )
            db.add(msg)
            db.commit()
            for event in pending_updates:
                events.put(frame(event))
            events.put(frame(Done(message_id=msg.id)))
    except Exception as exc:
        events.put(exc)
    finally:
        events.put(None)


def conversation(id, body, window, db, user, action="explain"):
    session = owned(db, Session, id, user)
    if session.status != "active":
        raise HTTPException(409, "会话已结束")
    require_profile(window)
    events = SimpleQueue()
    workers.submit(produce, id, body.content if body else "", window, user.id, events, action)
    first = events.get()
    if isinstance(first, Exception):
        if isinstance(first, ModelRequired):
            raise first
        if isinstance(first, (TeachingModelError, ModelError)):
            raise HTTPException(502, str(first)) from first
        raise first

    def frames():
        yield first
        while (item := events.get()) is not None:
            if isinstance(item, Exception):
                detail = str(item) if isinstance(item, (TeachingModelError, ModelError)) else "对话中断，请刷新历史以核对已保存的消息。"
                payload = {"detail": detail}
                if isinstance(item, ModelRequired):
                    payload.update(code="model_required", purpose=item.purpose)
                yield "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                return
            yield item

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{id}/opening/stream", response_class=StreamingResponse)
def opening(id: int, db=Depends(get_db), user=Depends(current_user)):
    """U0003 role-specific opening; repeated calls replay the saved opening."""
    return conversation(id, None, "feynman", db, user, action="opening")


@router.post("/{id}/lesson/stream", response_class=StreamingResponse)
def lesson(id: int, db=Depends(get_db), user=Depends(current_user)):
    return conversation(id, None, "encyclopedia", db, user, action="lesson")


@router.post("/{id}/feynman/stream", response_class=StreamingResponse)
def left(id: int, body: Turn, db=Depends(get_db), user=Depends(current_user)):
    return conversation(id, body, "feynman", db, user)


@router.post("/{id}/encyclopedia/stream", response_class=StreamingResponse)
def right(id: int, body: Turn, db=Depends(get_db), user=Depends(current_user)):
    return conversation(id, body, "encyclopedia", db, user)


@router.post("/{id}/report", response_model=ReportOut)
def finish(id: int, db=Depends(get_db), user=Depends(current_user)):
    with session_guard(id):
        return finish_locked(id, db, user)


def finish_locked(id, db, user):
    s = owned(db, Session, id, user)
    old = db.scalar(select(DiagnosticReport).where(DiagnosticReport.session_id == id))
    if old:
        db.commit()
        return public(old)
    data = model_teaching.diagnose(db, s, require_profile("report")).model_dump()
    lock_active(db, id)
    s.status = "completed"
    data["weakness_summary"] = json.dumps(data["weakness_summary"], ensure_ascii=False)
    row = DiagnosticReport(session_id=id, **data)
    db.add(row)
    s.ended_at = now()
    db.commit()
    return public(row)


@router.get("/{id}/report", response_model=ReportOut)
def report(id: int, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Session, id, user)
    row = db.scalar(select(DiagnosticReport).where(DiagnosticReport.session_id == id))
    if not row:
        raise HTTPException(404, "报告尚未生成")
    return public(row)
