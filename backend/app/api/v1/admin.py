from app.schemas.responses import StatsOut
import tempfile
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, func, or_, cast, String
from app.core.security import admin
from app.db.database import get_db, db_path
from app.db.models import (
    User,
    Document,
    KnowledgeNode,
    Session,
    WeaknessLog,
    DiagnosticReport,
    public,
)
from app.services.memory_service import delete_document
from app.services import data_export

router = APIRouter(prefix="/admin", tags=["后台"], dependencies=[Depends(admin)])
TABLES = {m.__tablename__: m for m in (Document, KnowledgeNode, WeaknessLog, DiagnosticReport)}


@router.get("/stats", response_model=StatsOut)
def stats(db=Depends(get_db)):
    counts = {
        k: db.scalar(select(func.count()).select_from(m))
        for k, m in [
            ("total_users", User),
            ("total_docs", Document),
            ("total_nodes", KnowledgeNode),
            ("total_sessions", Session),
        ]
    }
    n = func.count(WeaknessLog.id)
    top = db.execute(
        select(KnowledgeNode.id, KnowledgeNode.concept_name, n)
        .join(WeaknessLog)
        .group_by(KnowledgeNode.id)
        .order_by(n.desc(), KnowledgeNode.id)
        .limit(10)
    )
    return {
        **counts,
        "avg_mastery": round(db.scalar(select(func.avg(KnowledgeNode.mastery_score))) or 0, 2),
        "top_weaknesses": [
            {"node_id": id, "concept_name": name, "count": count} for id, name, count in top
        ],
    }


def table_model(name):
    if name not in TABLES:
        raise HTTPException(404, "不支持的数据表")
    return TABLES[name]


@router.get("/tables/{table_name}")
def records(
    table_name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query("", max_length=200),
    db=Depends(get_db),
):
    model = table_model(table_name)
    stmt = select(model)
    if q:
        stmt = stmt.where(
            or_(
                *(
                    cast(c, String).contains(q, autoescape=True)
                    for c in model.__table__.columns
                    if c.name != "file_path"
                )
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            public(row)
            for row in db.scalars(
                stmt.order_by(model.id).offset((page - 1) * page_size).limit(page_size)
            )
        ],
    }


@router.delete("/tables/{table_name}/{id}", status_code=204)
def remove(table_name: str, id: int, db=Depends(get_db)):
    row = db.get(table_model(table_name), id)
    if row is None:
        raise HTTPException(404, "记录不存在")
    if isinstance(row, Document):
        if row.parse_status == "processing":
            raise HTTPException(409, "正在解析")
        delete_document(db, row)
    else:
        db.delete(row)
        db.commit()


def export_copy():
    with tempfile.TemporaryDirectory(prefix="feynman-export-") as directory:
        path = Path(directory) / "feynman.db"
        data_export.write_sanitized_db(db_path, path)
        yield path


@router.get("/export/json")
def snapshot(path=Depends(export_copy, scope="request")):
    with data_export.snapshot_session(path) as db:
        data = data_export.snapshot_json(db)
    return JSONResponse(
        jsonable_encoder(data),
        headers={"Content-Disposition": 'attachment; filename="feynman.json"'},
    )


@router.get(
    "/export/db",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                "application/vnd.sqlite3": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
def download(path=Depends(export_copy, scope="request")):
    return FileResponse(path, filename="feynman.db", media_type="application/vnd.sqlite3")
