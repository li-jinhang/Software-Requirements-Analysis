from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.services.model_profiles import ModelError, ModelRequired
from app.services.chatgpt_login import end_login
from app.api.v1 import model_settings
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import auth, documents, sessions, admin
from app.core.config import settings
from app.core.security import signing_key
from app.db.database import Base, engine, SessionLocal
from app.db.review_migration import migrate_reviews
from app.db.seed import seed
from app.services.memory_service import recover_deletions
from sqlalchemy import select, update
from app.db.models import CognitiveItem, Document
from app.services import cognitive


@asynccontextmanager
async def lifespan(app):
    (settings.data_dir / "uploads").mkdir(exist_ok=True)
    signing_key()
    Base.metadata.create_all(engine)
    # SQLite create_all does not alter tables created by phase one.
    with engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        migrate_reviews(conn.exec_driver_sql)
        columns = conn.exec_driver_sql("PRAGMA table_info(knowledge_nodes)")
        if "key_points" not in {row[1] for row in columns}:
            conn.exec_driver_sql(
                "ALTER TABLE knowledge_nodes ADD COLUMN key_points JSON NOT NULL DEFAULT '[]'"
            )
        session_columns = conn.exec_driver_sql("PRAGMA table_info(sessions)")
        if "target_node_id" not in {row[1] for row in session_columns}:
            conn.exec_driver_sql(
                "ALTER TABLE sessions ADD COLUMN target_node_id INTEGER "
                "REFERENCES knowledge_nodes(id) ON DELETE SET NULL"
            )
    # With the single-worker deployment a processing row at startup is an interrupted ingest.
    with SessionLocal.begin() as db:
        recover_deletions(db)
        db.execute(
            update(Document)
            .where(Document.parse_status == "processing")
            .values(parse_status="failed", error_message="上次解析中断，可重新解析")
        )
    if settings.seed_demo:
        with SessionLocal() as db:
            seed(db)
    with SessionLocal.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        missing = select(Document.id).where(Document.parse_status == "completed",
            ~select(CognitiveItem.id).where(CognitiveItem.doc_id == Document.id).exists())
        for doc_id in db.scalars(missing).all():
            cognitive.from_nodes(db, doc_id)
    try:
        yield
    finally:
        end_login()


app = FastAPI(title="智溯费曼 · 混合记忆服务", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(ModelError)
async def model_error(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(ModelRequired)
async def model_required(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": "model_required", "purpose": exc.purpose, "message": str(exc)}},
    )


for router in (
    auth.router,
    documents.router,
    sessions.router,
    admin.router,
    model_settings.public_router,
    model_settings.router,
):
    app.include_router(router, prefix="/api/v1")
