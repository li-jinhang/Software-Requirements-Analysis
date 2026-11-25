"""Build a credential-free business snapshot without copying private provider tables."""

import json
import re
import sqlite3
from tempfile import TemporaryDirectory
from contextlib import closing, contextmanager
from pathlib import Path

from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.db.database import Base
from app.db.library_migration import migrate_document_library
from app.db.review_migration import migrate_reviews
from app.db.models import (
    User,
    RetiredAccountId,
    Document,
    KnowledgeNode,
    KnowledgeEdge,
    CognitiveItem,
    Session,
    Message,
    WeaknessLog,
    DiagnosticReport,
    public,
)

MODELS = (
    User,
    RetiredAccountId,
    Document,
    KnowledgeNode,
    KnowledgeEdge,
    Session,
    Message,
    WeaknessLog,
    DiagnosticReport,
    CognitiveItem,
)
MASK = "[REDACTED]"
PRIVATE_FIELD = re.compile(
    r"password|passwd|api[_-]?key|credential|secret|token|authorization|^(access|refresh)$", re.I
)
TOKEN = re.compile(
    r"(?i)\b(?:sk-[\w-]{12,}|AIza[\w-]{30,}|gh[pousr]_[\w]{20,}|eyJ[\w-]+\.[\w-]+\.[\w-]+)|\$2[aby]\$\d{2}\$[./\w]{53}"
)
BEARER = re.compile(r"(?i)\bBearer\s+[\w.~+/=-]+")
ASSIGNMENT = re.compile(
    r"""(?ix)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password_hash|client_secret)["']?\s*[:=]\s*["']?)([^"',;\s}\]]+)"""
)


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def private_values():
    values = {
        s.get_secret_value()
        for s in (settings.openai_api_key, settings.embedding_api_key, settings.jwt_secret)
    }
    signing = settings.data_dir / ".jwt-secret"
    if signing.is_file():
        values.add(signing.read_text().strip())
    path = settings.data_dir / "model-settings" / "profiles.sqlite3"
    if path.is_file():
        with closing(readonly(path)) as db:
            values.update(row[0] for row in db.execute("SELECT api_key FROM profiles"))
            for (encoded,) in db.execute("SELECT credentials FROM account"):
                values.update(
                    v
                    for k, v in json.loads(encoded).items()
                    if PRIVATE_FIELD.search(k) and isinstance(v, str)
                )
    capabilities = settings.data_dir / "capabilities" / "private.sqlite3"
    if capabilities.is_file():
        with closing(readonly(capabilities)) as db:
            values.update(row[0] for row in db.execute("SELECT token FROM servers"))
    return {v for v in values if v}


def redact(value, secrets):
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, dict):
        return {
            redact(k, secrets): redact(v, secrets)
            for k, v in value.items()
            if not PRIVATE_FIELD.search(k)
        }
    if not isinstance(value, str):
        return value
    for secret in secrets:
        value = value.replace(secret, MASK)
    value = TOKEN.sub(MASK, value)
    value = BEARER.sub("Bearer " + MASK, value)
    return ASSIGNMENT.sub(lambda m: m[1] + MASK, value)


@contextmanager
def snapshot_session(path):
    engine = create_engine(URL.create("sqlite", database=str(path)))
    try:
        with OrmSession(engine) as db:
            yield db
    finally:
        engine.dispose()


def write_sanitized_db(source, destination):
    """Backup includes committed WAL frames. Project its rows into a fresh, allowlisted schema."""
    destination = Path(destination)
    destination.touch(exist_ok=False)
    try:
        with TemporaryDirectory(dir=destination.parent) as work, closing(
            sqlite3.connect(Path(work) / "business.db")
        ) as copy:
            with closing(readonly(source)) as live:
                live.backup(copy)
            migrate_document_library(copy.execute)
            migrate_reviews(copy.execute)
            secrets = private_values() | {
                r[0] for r in copy.execute("SELECT password_hash FROM users") if r[0]
            }
            secrets = sorted(secrets, key=len, reverse=True)
            with snapshot_session(destination) as db:
                Base.metadata.create_all(db.get_bind(), tables=[m.__table__ for m in MODELS])
            legacy_rubric = False
            with closing(sqlite3.connect(destination)) as dest:
                dest.execute("PRAGMA foreign_keys=ON")
                # An older document can belong to a library with a higher ID.
                dest.execute("BEGIN")
                dest.execute("PRAGMA defer_foreign_keys=ON")
                for model in MODELS:
                    table = model.__tablename__
                    existing = {r[1] for r in copy.execute(f'PRAGMA table_info("{table}")')}
                    if not existing and model in (CognitiveItem, RetiredAccountId):
                        # These tables were introduced after the first release.
                        if model is CognitiveItem:
                            legacy_rubric = True
                        continue
                    cols = [c.name for c in model.__table__.columns]
                    fields = ",".join(f'"{c}"' for c in cols)
                    insert = (
                        f'INSERT INTO "{table}" ({fields}) VALUES ({",".join("?" for _ in cols)})'
                    )
                    # Match the application's additive migrations even when exporting an unbooted checkout.
                    legacy_defaults = {("sessions", "target_node_id"): "NULL",
                                       ("knowledge_nodes", "key_points"): "'[]'"}
                    source_fields = ",".join(
                        f'"{table}"."{col}"' if col in existing else legacy_defaults[table, col]
                        for col in cols
                    )
                    for row in copy.execute(f'SELECT {source_fields} FROM "{table}" ORDER BY id'):
                        clean = dict(zip(cols, row))
                        for col, value in clean.items():
                            if PRIVATE_FIELD.search(col) or col == "file_path":
                                clean[col] = ""
                            elif value is not None and col in (
                                "key_points",
                                "target_item_ids",
                                "related_node_ids",
                                "meta_json",
                                "weakness_summary",
                                "library_setup",
                            ):
                                clean[col] = json.dumps(
                                    redact(json.loads(value), secrets), ensure_ascii=False
                                )
                            else:
                                clean[col] = redact(value, secrets)
                        if model is User:
                            clean.update(
                                username=f"user-{clean['id']}", nickname=f"用户 {clean['id']}"
                            )
                        dest.execute(insert, tuple(clean.values()))
                dest.commit()
                if legacy_rubric:
                    from app.services.cognitive import from_nodes
                    with snapshot_session(destination) as db:
                        for doc_id in db.scalars(select(Document.id).where(Document.parse_status == "completed")).all():
                            from_nodes(db, doc_id)
                        db.commit()
                dest.execute("VACUUM")
                if dest.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise ValueError("脱敏副本完整性检查失败")
            return secrets
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def snapshot_json(db):
    return {
        m.__tablename__: [public(row) for row in db.scalars(select(m).order_by(m.id))]
        for m in MODELS
    }
