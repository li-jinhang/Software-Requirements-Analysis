from datetime import timedelta
from app.schemas.responses import CognitiveDocOut, DocumentOut, NodeOut, GraphOut, ContextOut, LearningStep, ReviewListsOut
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, Form, UploadFile, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, update
from app.core.security import current_user
from app.core.config import settings
from app.db.database import get_db
from app.db.models import CognitiveItem, Document, KnowledgeNode, now, public
from app.schemas import Note, TimeAdvance
from app.services import cognitive, memory_service as memory
from app.services.model_profiles import require_profile

router = APIRouter(prefix="/documents", tags=["资料"])
LIMIT = 20 * 1024**2


def owned(db, model, id, user):
    obj = db.get(model, id)
    if not obj or obj.user_id != user.id:
        raise HTTPException(404, "资源不存在")
    return obj


@router.get("/{doc_id}/due-reviews", response_model=ReviewListsOut)
def due_reviews(doc_id: int, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Document, doc_id, user)
    return cognitive.review_lists(db, doc_id)


@router.post("/{doc_id}/reviews/{item_id}/skip", response_model=ReviewListsOut)
def skip_review(doc_id: int, item_id: int, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Document, doc_id, user)
    item = db.get(CognitiveItem, item_id)
    if item is None or item.doc_id != doc_id:
        raise HTTPException(404, "条目不属于当前资料")
    when = now()
    # An already-handled reminder is idempotent, even across tabs.
    db.execute(update(CognitiveItem).where(CognitiveItem.id == item_id,
        CognitiveItem.status != "untested", CognitiveItem.next_review_at <= when
    ).values(skip_count=CognitiveItem.skip_count + 1, next_review_at=when + timedelta(days=1))
      .execution_options(synchronize_session=False))
    db.commit()
    db.expire_all()
    return cognitive.review_lists(db, doc_id)


@router.post("/{doc_id}/simulate-time-advance", response_model=ReviewListsOut)
def simulate_time(doc_id: int, body: TimeAdvance, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Document, doc_id, user)
    db.execute(update(CognitiveItem).where(CognitiveItem.doc_id == doc_id,
        CognitiveItem.status != "untested", CognitiveItem.next_review_at.is_not(None)
    ).values(next_review_at=func.strftime("%Y-%m-%d %H:%M:%f", CognitiveItem.next_review_at,
                                         f"-{body.days} days")).execution_options(synchronize_session=False))
    db.commit()
    return cognitive.review_lists(db, doc_id)


@router.post("/generate-from-topic", status_code=201, response_model=DocumentOut)
def generate_from_topic(body: cognitive.Topic, db=Depends(get_db), user=Depends(current_user)):
    return cognitive.generate(db, user.id, body, require_profile("extract"))


@router.get("/{doc_id}/cognitive-doc", response_model=CognitiveDocOut)
def cognitive_doc(doc_id: int, session_id: int | None = None, db=Depends(get_db), user=Depends(current_user)):
    from app.db.models import Session
    owned(db, Document, doc_id, user)
    previous = target = None
    if session_id is not None:
        session = owned(db, Session, session_id, user)
        if session.doc_id != doc_id:
            raise HTTPException(404, "会话不属于该资料")
        if session.mode == "review":
            result = cognitive.document(db, doc_id)
            remaining = session.target_item_ids[session.review_cursor:]
            result["current_item_id"] = next(iter(remaining), None)
            return result
        previous = cognitive.last_focus(db, session_id)
        target = session.target_node_id
    return cognitive.document(db, doc_id, previous, target)


@router.post("/upload", status_code=201, response_model=DocumentOut)
def upload(
    file: UploadFile,
    subject: str = Form("", max_length=100),
    db=Depends(get_db),
    user=Depends(current_user),
):
    name = Path(file.filename or "untitled").name
    ext = Path(name).suffix.lower()
    if ext not in {".txt", ".md", ".docx", ".pdf"}:
        raise HTTPException(415, "支持 PDF、DOCX、MD、TXT")
    profile = require_profile("extract")
    path = settings.data_dir / "uploads" / (uuid4().hex + ext)
    try:
        with path.open("xb") as f:
            total = 0
            while block := file.file.read(1024 * 1024):
                total += len(block)
                if total > LIMIT:
                    raise HTTPException(413, "最大 20 MB")
                f.write(block)
        doc = Document(
            user_id=user.id,
            title=Path(name).stem,
            subject=subject,
            file_type=ext[1:],
            file_path=str(path),
        )
        db.add(doc)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return run_ingest(db, doc.id, profile)


def run_ingest(db, id, profile):
    try:
        return memory.ingest_document(db, id, profile)
    except ValueError as exc:
        raise HTTPException(422, {"doc_id": id, "message": str(exc)})


@router.post("/{id}/retry", response_model=DocumentOut)
def retry(id: int, db=Depends(get_db), user=Depends(current_user)):
    if owned(db, Document, id, user).parse_status != "failed":
        raise HTTPException(409, "仅失败的文档可以重试")
    return run_ingest(db, id, require_profile("extract"))


@router.get("", response_model=list[DocumentOut])
def documents(db=Depends(get_db), user=Depends(current_user)):
    return [
        public(d)
        for d in db.scalars(
            select(Document).where(Document.user_id == user.id).order_by(Document.id.desc())
        )
    ]


@router.get("/{id}", response_model=DocumentOut)
def detail(id: int, db=Depends(get_db), user=Depends(current_user)):
    return public(owned(db, Document, id, user))


@router.delete("/{id}", status_code=204)
def delete(id: int, db=Depends(get_db), user=Depends(current_user)):
    doc = owned(db, Document, id, user)
    if doc.parse_status == "processing":
        raise HTTPException(409, "正在解析")
    memory.delete_document(db, doc)


@router.get("/{id}/memory", response_model=ContextOut, response_model_exclude_unset=True)
def archive(id: int, db=Depends(get_db), user=Depends(current_user), query: str | None = None):
    owned(db, Document, id, user)
    return memory.get_memory_context(db, id, query=query)


@router.patch("/{id}/nodes/{node_id}/note", response_model=NodeOut)
def note(id: int, node_id: int, body: Note, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Document, id, user)
    node = db.get(KnowledgeNode, node_id)
    if not node or node.doc_id != id:
        raise HTTPException(404, "节点不存在")
    node.user_note = body.user_note
    db.commit()
    return public(node)


@router.get("/{id}/memory/export")
def export(
    id: int, format: Literal["md", "json"] = "md", db=Depends(get_db), user=Depends(current_user)
):
    owned(db, Document, id, user)
    headers = {"Content-Disposition": f'attachment; filename="memory-{id}.{format}"'}
    if format == "json":
        return JSONResponse(jsonable_encoder(memory.export_memory_json(db, id)), headers=headers)
    return Response(
        memory.export_memory_markdown(db, id), media_type="text/markdown", headers=headers
    )


@router.get("/{id}/graph", response_model=GraphOut)
def graph(id: int, db=Depends(get_db), user=Depends(current_user)):
    ctx = archive(id, db, user)
    return {"nodes": ctx["nodes"], "edges": ctx["edges"]}


@router.get("/{doc_id}/learning-path", response_model=list[LearningStep])
def learning_path(doc_id: int, db=Depends(get_db), user=Depends(current_user)):
    owned(db, Document, doc_id, user)
    return memory.compute_learning_path(db, doc_id)
