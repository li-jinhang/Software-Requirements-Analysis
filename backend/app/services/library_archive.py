"""Transfer a course library and its sources between learning accounts."""

import logging
from contextlib import suppress
from typing import Literal
from zipfile import ZipFile

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator
from sqlalchemy import select

from app.db.models import CognitiveItem, Document, KnowledgeEdge, KnowledgeNode, public
from app.schemas import Relation
from app.services import rag_store
from app.services.portable_archive import (
    MAX_MANIFEST, restore_source, saved_chunks, source_file, verify_entries, write_archive,
)

MANIFEST = "library.json"
log = logging.getLogger(__name__)


class LibraryBusy(ValueError):
    pass


class ArchiveRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ArchiveNode(ArchiveRecord):
    id: PositiveInt
    concept_name: str
    summary: str
    key_points: list[str]
    importance: int = Field(ge=1, le=5)


class ArchiveEdge(ArchiveRecord):
    source_node_id: PositiveInt
    target_node_id: PositiveInt
    relation_type: Relation
    description: str


class ArchiveItem(ArchiveRecord):
    node_id: PositiveInt | None
    section_title: str
    item_order: int = Field(ge=0, le=2**63 - 1)
    statement: str
    key_criteria: str


class ArchiveDocument(ArchiveRecord):
    id: PositiveInt
    title: str = Field(min_length=1)
    subject: str
    file_type: Literal["pdf", "docx", "md", "txt"]
    structured_summary: str
    parse_status: Literal["completed", "failed"]
    has_source: bool
    chunks: list[str]
    nodes: list[ArchiveNode]
    edges: list[ArchiveEdge]
    items: list[ArchiveItem]

    @property
    def entry(self):
        return f"sources/{self.id}.{self.file_type}" if self.has_source else None

    @model_validator(mode="after")
    def check_graph(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes) or len({item.item_order for item in self.items}) != len(self.items):
            raise ValueError("资料中存在重复节点或认知条目。")
        if any(edge.source_node_id not in ids or edge.target_node_id not in ids for edge in self.edges):
            raise ValueError("知识图谱引用了不存在的节点。")
        if any(item.node_id is not None and item.node_id not in ids for item in self.items):
            raise ValueError("认知条目引用了不存在的节点。")
        return self


class LibraryArchive(ArchiveRecord):
    version: Literal[1]
    library: ArchiveDocument
    sources: list[ArchiveDocument]

    @model_validator(mode="after")
    def check_library(self):
        ids = {source.id for source in self.sources}
        if self.library.id in ids or len(ids) != len(self.sources):
            raise ValueError("资料库包含重复资料。")
        if self.library.parse_status != "completed":
            raise ValueError("资料库尚未完成。")
        return self


def export_library(db, library, path):
    sources = list(db.scalars(select(Document).where(
        Document.library_id == library.id, Document.user_id == library.user_id,
        Document.is_library.is_(False),
    ).order_by(Document.id)))
    if library.parse_status != "completed" or any(src.parse_status == "processing" for src in sources):
        raise LibraryBusy("请等待课程框架或补充资料处理完成后再导出。")
    files = {}

    def describe(row):
        def rows(table, schema):
            return [schema.model_validate(value) for value in db.scalars(
                select(table).where(table.doc_id == row.id).order_by(table.id)
            )]

        source = source_file(row)
        if not row.is_library and row.file_path and source is None:
            raise ValueError("原始资料文件不可用。")
        chunks = saved_chunks(row.id) if row.chunk_count else []
        if len(chunks) != row.chunk_count:
            raise ValueError("资料索引片段不完整。")
        record = ArchiveDocument(
            id=row.id, title=row.title, subject=row.subject, file_type=row.file_type,
            structured_summary=row.structured_summary, has_source=source is not None, chunks=chunks,
            parse_status="completed" if row.parse_status == "completed" else "failed",
            nodes=rows(KnowledgeNode, ArchiveNode), edges=rows(KnowledgeEdge, ArchiveEdge),
            items=rows(CognitiveItem, ArchiveItem),
        )
        if source is not None:
            files[record.entry] = source
        return record

    bundle = LibraryArchive(version=1, library=describe(library), sources=[describe(row) for row in sources])
    write_archive(path, MANIFEST, bundle.model_dump_json().encode("utf-8"), files)


def import_library(db, user, file):
    paths, indexed = [], []
    with ZipFile(file) as archive:
        if archive.getinfo(MANIFEST).file_size > MAX_MANIFEST:
            raise ValueError("资料库索引过大。")
        bundle = LibraryArchive.model_validate_json(archive.read(MANIFEST))
        records = [bundle.library, *bundle.sources]
        verify_entries(archive, MANIFEST, (record.entry for record in records if record.entry))
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            titles = {title.casefold() for title in db.scalars(select(Document.title).where(
                Document.user_id == user.id, Document.is_library.is_(True),
            ))}
            original = title = bundle.library.title
            sequence = 1
            while title.casefold() in titles:
                suffix = "（导入）" if sequence == 1 else f"（导入 {sequence}）"
                title = original + suffix
                sequence += 1
            root, root_graph = restore_document(db, user, archive, bundle.library, title, None, paths)
            documents = [{"document": public(root), "graph": root_graph}]
            for record in bundle.sources:
                row, graph = restore_document(db, user, archive, record, record.title, root.id, paths)
                documents.append({"document": public(row), "graph": graph})
            for record, restored in zip(records, documents, strict=True):
                if record.chunks:
                    row = restored["document"]
                    indexed.append(row["id"])
                    rag_store.remove(row["id"])
                    rag_store.index(row["id"], row["title"], record.chunks, local_only=True)
            db.commit()
            return documents
        except Exception:
            # Keep the write lock during cleanup so a rolled-back ID cannot be reused here.
            for doc_id in indexed:
                try:
                    rag_store.remove(doc_id)
                except Exception:
                    log.exception("无法清理未导入资料的索引 %s", doc_id)
            for path in paths:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            db.rollback()
            raise


def restore_document(db, user, archive, record, title, library_id, paths):
    row = Document(
        user_id=user.id, is_library=library_id is None, library_id=library_id, title=title,
        subject=record.subject, file_type=record.file_type, structured_summary=record.structured_summary,
        file_path=restore_source(archive, record.entry, record.file_type, paths) if record.entry else "",
        parse_status=record.parse_status, chunk_count=len(record.chunks),
        error_message="导入后可重新解析" if record.parse_status == "failed" and record.entry else "",
    )
    db.add(row)
    db.flush()
    nodes = {
        node.id: KnowledgeNode(doc_id=row.id, **node.model_dump(exclude={"id"})) for node in record.nodes
    }
    db.add_all(nodes.values())
    db.flush()
    edges = [KnowledgeEdge(
        doc_id=row.id, source_node_id=nodes[edge.source_node_id].id, target_node_id=nodes[edge.target_node_id].id,
        relation_type=edge.relation_type, description=edge.description,
    ) for edge in record.edges]
    db.add_all(edges)
    db.add_all(CognitiveItem(
        doc_id=row.id, node_id=nodes[item.node_id].id if item.node_id is not None else None,
        **item.model_dump(exclude={"node_id"}),
    ) for item in record.items)
    db.flush()
    return row, {"nodes": [public(node) for node in nodes.values()], "edges": [public(edge) for edge in edges]}
