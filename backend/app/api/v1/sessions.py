from app.schemas.responses import SessionOut, MessageOut, ReportOut
import json
from threading import Lock
from app.agents import model_teaching
from app.services.model_profiles import ModelError, require_profile
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from app.core.security import current_user
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue
from app.db.database import get_db, SessionLocal

# Serialize turns and completion per session without holding a SQLite write lock during inference.
session_locks = [Lock() for _ in range(127)]

workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feynman-turn")
from app.db.models import Session, Document, Message, DiagnosticReport, now, public
from app.api.v1.documents import owned
from app.schemas import SessionCreate, PersonaChange, Turn
from app.agents.contracts import (
    AgentInput, MemoryUpdate, Citation, Token, Done, Preview, StreamError, EVENT_NAMES,
)

router = APIRouter(prefix="/sessions", tags=["会话"])


def lock_active(db, id):
    if not db.execute(
        update(Session).where(Session.id == id, Session.status == "active").values(status="active")
    ).rowcount:
        raise HTTPException(409, "会话已结束")


@router.post("", status_code=201, response_model=SessionOut)
def create(body: SessionCreate, db=Depends(get_db), user=Depends(current_user)):
    if owned(db, Document, body.doc_id, user).parse_status != "completed":
        raise HTTPException(409, "资料尚未解析完成")
    require_profile("feynman")
    row = Session(user_id=user.id, **body.model_dump())
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
    s = owned(db, Session, id, user)
    lock_active(db, id)
    s.persona_type = body.persona_type
    db.commit()
    return public(s)


@router.get("/{id}/messages", response_model=list[MessageOut])
def history(id: int, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Session, id, user)
    return [
        public(m)
        for m in db.scalars(select(Message).where(Message.session_id == id).order_by(Message.id))
    ]


def frame(event):
    return f"event: {EVENT_NAMES[type(event)]}\ndata: {event.model_dump_json()}\n\n"


def produce(id, content, window, user_id, events):
    """Finish the accepted turn even if the HTTP client disconnects."""
    try:
        with session_locks[id % len(session_locks)], SessionLocal() as db:
            s = db.get(Session, id)
            if s is None or s.user_id != user_id:
                raise HTTPException(404, "会话不存在")
            turn = AgentInput(
                session_id=id, doc_id=s.doc_id, persona_type=s.persona_type, content=content
            )
            if s.status != "active":
                raise HTTPException(409, "会话已结束")
            prepared = model_teaching.prepare(
                db, turn, window, require_profile(window),
                emit_preview=lambda text: events.put(frame(Preview(text=text))),
            )
            lock_active(db, id)
            db.add(Message(session_id=id, window_type=window, sender_role="user", content=content))
            text = []
            meta = {}
            node_ids = []
            for event in model_teaching.apply(db, turn, prepared):
                if isinstance(event, Token):
                    text.append(event.delta)
                elif isinstance(event, Citation):
                    meta["citations"] = event.model_dump()["citations"]
                elif isinstance(event, MemoryUpdate):
                    meta["updates"] = event.model_dump()["updates"]
                    node_ids.extend(n.node_id for n in event.updates)
                events.put(frame(event))
            msg = Message(
                session_id=id,
                window_type=window,
                sender_role="assistant",
                content="".join(text),
                related_node_ids=json.dumps(node_ids),
                meta_json=json.dumps(meta, ensure_ascii=False),
            )
            db.add(msg)
            db.commit()
            events.put(frame(Done(message_id=msg.id)))
    except Exception as exc:
        events.put(exc)
    finally:
        events.put(None)


def conversation(id, body, window, db, user):
    session = owned(db, Session, id, user)
    if session.status != "active":
        raise HTTPException(409, "会话已结束")
    events = SimpleQueue()
    workers.submit(produce, id, body.content, window, user.id, events)
    first = events.get()
    if isinstance(first, Exception):
        raise first

    def frames():
        yield first
        while (item := events.get()) is not None:
            if isinstance(item, Exception):
                detail = str(item) if isinstance(item, ModelError) else "对话中断，未提交本轮记忆"
                yield frame(StreamError(detail=detail))
                break
            yield item

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{id}/feynman/stream", response_class=StreamingResponse)
def left(id: int, body: Turn, db=Depends(get_db), user=Depends(current_user)):
    return conversation(id, body, "feynman", db, user)


@router.post("/{id}/encyclopedia/stream", response_class=StreamingResponse)
def right(id: int, body: Turn, db=Depends(get_db), user=Depends(current_user)):
    return conversation(id, body, "encyclopedia", db, user)


@router.post("/{id}/report", response_model=ReportOut)
def finish(id: int, db=Depends(get_db), user=Depends(current_user)):
    with session_locks[id % len(session_locks)]:
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
