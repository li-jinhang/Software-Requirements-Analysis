from app.schemas.responses import DocumentOut, NodeOut, GraphOut, ContextOut, LearningStep
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, Form, UploadFile, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from app.core.security import current_user
from app.core.config import settings
from app.db.database import get_db
from app.db.models import Document, KnowledgeNode, public
from app.schemas import Note
from app.services import memory_service as memory

router = APIRouter(prefix="/documents", tags=["资料"])
LIMIT = 20 * 1024**2


def owned(db, model, id, user):
    obj = db.get(model, id)
    if not obj or obj.user_id != user.id:
        raise HTTPException(404, "资源不存在")
    return obj


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
    return run_ingest(db, doc.id)


def run_ingest(db, id):
    try:
        return memory.ingest_document(db, id)
    except ValueError as exc:
        raise HTTPException(422, {"doc_id": id, "message": str(exc)})


@router.post("/{id}/retry", response_model=DocumentOut)
def retry(id: int, db=Depends(get_db), user=Depends(current_user)):
    if owned(db, Document, id, user).parse_status != "failed":
        raise HTTPException(409, "仅失败的文档可以重试")
    return run_ingest(db, id)


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
