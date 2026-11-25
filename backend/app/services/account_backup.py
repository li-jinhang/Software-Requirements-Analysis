"""Portable backups of one local account, with fresh IDs on restore."""

from contextlib import suppress
import json
from typing import Literal
from uuid import uuid4
from zipfile import ZipFile

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.models import (
    CognitiveItem, DiagnosticReport, Document, KnowledgeEdge, KnowledgeNode,
    Message, Session, User, WeaknessLog, next_account_id, public,
)
from app.schemas.responses import (
    CognitiveItemOut, DocumentOut, EdgeOut, MessageOut, NodeOut, ReportOut, SessionOut,
    WeaknessOut,
)
from app.services import rag_store
from app.services.portable_archive import (
    MAX_BACKUP, MAX_MANIFEST, restore_source, saved_chunks, source_file, verify_entries, write_archive,
)


MANIFEST = "account.json"


class SavedDocument(DocumentOut):
    library_id: int | None = None
    file_type: Literal["pdf", "docx", "md", "txt"]
    has_source: bool
    chunks: list[str]

    def entry(self):
        return f"sources/{self.id}.{self.file_type}"


class SavedSession(SessionOut):
    review_cursor: int = Field(ge=0)


class AccountBackup(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    version: Literal[1]
    name: str = Field(min_length=1, max_length=64)
    documents: list[SavedDocument]
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    items: list[CognitiveItemOut]
    sessions: list[SavedSession]
    messages: list[MessageOut]
    weaknesses: list[WeaknessOut]
    reports: list[ReportOut]


def new_account(db, name, *, imported=False):
    # A restore is always a new account, even on the computer that made the backup.
    existing = {value.casefold() for value in db.scalars(select(User.nickname))}
    label = name
    index = 1
    while label.casefold() in existing:
        if not imported:
            raise ValueError("账号名称已存在。")
        suffix = "（导入）" if index == 1 else f"（导入 {index}）"
        label = name[:64 - len(suffix)] + suffix
        index += 1
    user = User(id=next_account_id(db), username="local_" + uuid4().hex, nickname=label, password_hash="")
    db.add(user)
    db.flush()
    return user


def export_account(db, user, path):
    db.connection().exec_driver_sql("BEGIN")
    docs = select(Document.id).where(Document.user_id == user.id)
    sessions = select(Session.id).where(Session.user_id == user.id, Session.doc_id.in_(docs))

    def rows(model, condition):
        return [public(row) for row in db.scalars(select(model).where(condition).order_by(model.id))]

    files = {}
    documents = []
    for row in db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.id)):
        source = source_file(row)
        doc = SavedDocument(**public(row), has_source=source is not None, chunks=saved_chunks(row.id))
        documents.append(doc)
        if source is not None:
            files[doc.entry()] = source
    backup = AccountBackup(
        version=1, name=user.nickname, documents=documents,
        nodes=rows(KnowledgeNode, KnowledgeNode.doc_id.in_(docs)),
        edges=rows(KnowledgeEdge, KnowledgeEdge.doc_id.in_(docs)),
        items=rows(CognitiveItem, CognitiveItem.doc_id.in_(docs)),
        sessions=rows(Session, Session.id.in_(sessions)),
        messages=rows(Message, Message.session_id.in_(sessions)),
        weaknesses=rows(WeaknessLog, WeaknessLog.session_id.in_(sessions)),
        reports=rows(DiagnosticReport, DiagnosticReport.session_id.in_(sessions)),
    )
    db.commit()
    write_archive(path, MANIFEST, backup.model_dump_json().encode("utf-8"), files)


def import_account(db, file):
    paths, indexed = [], []
    try:
        with ZipFile(file) as archive:
            if archive.getinfo(MANIFEST).file_size > MAX_MANIFEST:
                raise ValueError("备份索引过大。")
            backup = AccountBackup.model_validate_json(archive.read(MANIFEST))
            verify_entries(archive, MANIFEST, (doc.entry() for doc in backup.documents if doc.has_source))
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            user = new_account(db, backup.name, imported=True)
            restore(db, user, archive, backup, paths, indexed)
            db.commit()
            return user
    except BaseException:
        db.rollback()
        for id in indexed:
            with suppress(Exception):
                rag_store.remove(id)
        for path in paths:
            with suppress(OSError):
                path.unlink(missing_ok=True)
        raise


def restore(db, user, archive, backup, paths, indexed):
    def insert(model, records, values):
        rows = {}
        for record in records:
            if record.id <= 0 or record.id in rows:
                raise ValueError("备份包含重复或无效的记录编号。")
            row = model(**values(record))
            rows[record.id] = row
            db.add(row)
        db.flush()
        return rows

    def document_values(record):
        path = restore_source(archive, record.entry() if record.has_source else None, record.file_type, paths)
        values = record.model_dump(exclude={"id", "user_id", "has_source", "chunks", "library_id"})
        if record.parse_status == "processing" or (record.parse_status == "pending" and not record.library_setup):
            values.update(parse_status="failed", error_message=(
                "导入后可重新生成" if record.library_setup else "导入后可重新解析"
            ))
        return {**values, "user_id": user.id, "file_path": path, "chunk_count": len(record.chunks)}

    docs = insert(Document, backup.documents, document_values)
    for record in backup.documents:
        if record.library_id is not None:
            library = docs.get(record.library_id)
            if record.is_library or not library or not library.is_library:
                raise ValueError("备份中的资料库归属无效。")
            docs[record.id].library_id = library.id

    def doc_values(record, exclude=()):
        return {**record.model_dump(exclude={"id", "doc_id", *exclude}), "doc_id": docs[record.doc_id].id}

    nodes = insert(KnowledgeNode, backup.nodes, doc_values)

    def concept(id, doc_id):
        if id is None:
            return None
        value = nodes[id]
        if value.doc_id != doc_id:
            raise ValueError("知识点与资料编号不一致。")
        return value.id

    def item_values(record):
        values = doc_values(record, ("node_id",))
        return {**values, "node_id": concept(record.node_id, values["doc_id"])}

    items = insert(CognitiveItem, backup.items, item_values)

    def edge_values(record):
        values = doc_values(record, ("source_node_id", "target_node_id"))
        return {**values, "source_node_id": concept(record.source_node_id, values["doc_id"]),
                "target_node_id": concept(record.target_node_id, values["doc_id"])}

    insert(KnowledgeEdge, backup.edges, edge_values)

    def session_values(record):
        values = doc_values(record, ("user_id", "target_node_id", "target_item_ids"))
        selected = [items[id] for id in record.target_item_ids]
        if any(item.doc_id != values["doc_id"] for item in selected):
            raise ValueError("复习条目与资料编号不一致。")
        return {**values, "user_id": user.id,
                "target_node_id": concept(record.target_node_id, values["doc_id"]),
                "target_item_ids": [item.id for item in selected]}

    sessions = insert(Session, backup.sessions, session_values)

    def session_payload(record, exclude=()):
        return {**record.model_dump(exclude={"id", "session_id", *exclude}),
                "session_id": sessions[record.session_id].id}

    def message_values(record):
        values = session_payload(record, ("meta_json", "related_node_ids"))
        doc_id = sessions[record.session_id].doc_id
        ids = [nodes[id].id for id in record.related_node_ids if id in nodes and nodes[id].doc_id == doc_id]
        return {**values, "related_node_ids": json.dumps(ids), "meta_json": "{}"}

    messages = insert(Message, backup.messages, message_values)
    for record in backup.messages:
        messages[record.id].meta_json = json.dumps(remap_meta(record.meta_json, {
            "doc_id": docs, "session_id": sessions, "node_id": nodes,
            "target_node_id": nodes, "item_id": items, "current_item_id": items,
            "message_id": messages,
        }), ensure_ascii=False)

    insert(WeaknessLog, backup.weaknesses, lambda row: {
        **session_payload(row, ("node_id",)),
        "node_id": concept(row.node_id, sessions[row.session_id].doc_id),
    })
    insert(DiagnosticReport, backup.reports, lambda row: {
        **session_payload(row, ("weakness_summary",)),
        "weakness_summary": json.dumps(row.weakness_summary, ensure_ascii=False),
    })
    for doc in backup.documents:
        if doc.chunks:
            row = docs[doc.id]
            indexed.append(row.id)
            rag_store.remove(row.id)
            rag_store.index(row.id, row.title, doc.chunks, local_only=True)


def remap_meta(value, references):
    if isinstance(value, dict):
        return {
            key: (references[key][item].id if type(item) is int and item in references[key] else None)
            if key in references else remap_meta(item, references)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [remap_meta(item, references) for item in value]
    return value
