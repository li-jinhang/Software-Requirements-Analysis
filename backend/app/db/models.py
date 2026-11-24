from datetime import datetime, timezone
from typing import Annotated
from sqlalchemy import JSON, ForeignKey, CheckConstraint, Text, inspect
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


PK = Annotated[int, mapped_column(primary_key=True)]
Created = Annotated[datetime, mapped_column(default=now)]


def fk(target):
    return mapped_column(ForeignKey(target + ".id", ondelete="CASCADE"), index=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role in ('student','admin')"),)
    id: Mapped[PK]
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    nickname: Mapped[str]
    role: Mapped[str] = mapped_column(default="student")
    created_at: Mapped[Created]


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("parse_status in ('pending','processing','completed','failed')"),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[PK]
    user_id: Mapped[int] = fk("users")
    title: Mapped[str]
    subject: Mapped[str]
    file_type: Mapped[str]
    file_path: Mapped[str]
    parse_status: Mapped[str] = mapped_column(default="pending")
    error_message: Mapped[str] = mapped_column(default="")
    chunk_count: Mapped[int] = mapped_column(default=0)
    structured_summary: Mapped[str] = mapped_column(default="")
    created_at: Mapped[Created]


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (
        CheckConstraint("importance between 1 and 5 and mastery_score between 0 and 100"),
        CheckConstraint("status in ('unlearned','weak','learning','mastered')"),
    )
    id: Mapped[PK]
    doc_id: Mapped[int] = fk("documents")
    concept_name: Mapped[str]
    summary: Mapped[str]
    key_points: Mapped[list[str]] = mapped_column(JSON,default=list,server_default='[]')
    importance: Mapped[int]
    mastery_score: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="unlearned")
    lookup_count: Mapped[int] = mapped_column(default=0)
    user_note: Mapped[str] = mapped_column(default="")
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        CheckConstraint("relation_type in ('prerequisite','contains','contrast','applies_to')"),
    )
    id: Mapped[PK]
    doc_id: Mapped[int] = fk("documents")
    source_node_id: Mapped[int] = fk("knowledge_nodes")
    target_node_id: Mapped[int] = fk("knowledge_nodes")
    relation_type: Mapped[str]
    description: Mapped[str]


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("persona_type in ('child','peer','examiner')"),
        CheckConstraint("status in ('active','completed')"),
    )
    id: Mapped[PK]
    user_id: Mapped[int] = fk("users")
    doc_id: Mapped[int] = fk("documents")
    title: Mapped[str]
    persona_type: Mapped[str]
    target_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[Created]
    ended_at: Mapped[datetime | None]


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("window_type in ('feynman','encyclopedia')"),
        CheckConstraint("sender_role in ('user','assistant','system')"),
    )
    id: Mapped[PK]
    session_id: Mapped[int] = fk("sessions")
    window_type: Mapped[str]
    sender_role: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    related_node_ids: Mapped[str] = mapped_column(default="[]")
    meta_json: Mapped[str] = mapped_column(default="{}")
    created_at: Mapped[Created]


class WeaknessLog(Base):
    __tablename__ = "weakness_logs"
    __table_args__ = (
        CheckConstraint("weakness_type in ('omission','misconception','jargon','need_lookup')"),
    )
    id: Mapped[PK]
    session_id: Mapped[int] = fk("sessions")
    node_id: Mapped[int] = fk("knowledge_nodes")
    weakness_type: Mapped[str]
    detail: Mapped[str]
    is_resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[Created]


class DiagnosticReport(Base):
    __tablename__ = "diagnostic_reports"
    id: Mapped[PK]
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True
    )
    coverage_score: Mapped[int]
    accuracy_score: Mapped[int]
    clarity_score: Mapped[int]
    logic_score: Mapped[int]
    overall_comment: Mapped[str]
    weakness_summary: Mapped[str]
    review_plan: Mapped[str]
    created_at: Mapped[Created]


def public(row):
    import json

    result = {
        col.key: getattr(row, col.key)
        for col in inspect(row).mapper.columns
        if col.key not in {"password_hash", "file_path"}
    }
    for key in ("related_node_ids", "meta_json", "weakness_summary"):
        if key in result:
            result[key] = json.loads(result[key])
    return result
