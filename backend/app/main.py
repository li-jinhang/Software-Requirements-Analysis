from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import auth, documents, sessions, admin
from app.core.config import settings
from app.core.security import signing_key
from app.db.database import Base, engine, SessionLocal
from app.db.seed import seed
from app.services.memory_service import recover_deletions
from sqlalchemy import update
from app.db.models import Document


@asynccontextmanager
async def lifespan(app):
    (settings.data_dir / "uploads").mkdir(exist_ok=True)
    signing_key()
    Base.metadata.create_all(engine)
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
    yield


app = FastAPI(title="智溯费曼 · 混合记忆服务", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],
)
for router in (auth.router, documents.router, sessions.router, admin.router):
    app.include_router(router, prefix="/api/v1")
