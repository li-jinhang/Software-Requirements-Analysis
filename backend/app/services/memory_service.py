"""Shared SQL + RAG services. Mutating agent methods flush; caller owns the transaction."""

import re
from pathlib import Path
from collections import defaultdict
from tempfile import NamedTemporaryFile
from app.core.config import settings
from sqlalchemy import select, update, func, case

from app.db.models import Document, KnowledgeNode, KnowledgeEdge, Session, WeaknessLog, public
from app.services import doc_parser, rag_store, llm_client


def ingest_document(db, doc_id):
    claimed = db.execute(
        update(Document)
        .where(Document.id == doc_id, Document.parse_status.in_(["pending", "failed"]))
        .values(parse_status="processing", error_message="")
    ).rowcount
    if not claimed:
        db.rollback()
        raise ValueError("文档不存在或已处理")
    db.commit()
    doc = db.get(Document, doc_id)
    try:
        text = doc_parser.parse(doc.file_path)
        chunks = doc_parser.split_chunks(text)
        extracted = llm_client.extract(text, doc.title)
        rag_store.remove(doc_id)
        rag_store.index(doc_id, doc.title, chunks)
        nodes = {
            c.concept_name: KnowledgeNode(doc_id=doc_id, **c.model_dump()) for c in extracted.nodes
        }
        db.add_all(nodes.values())
        db.flush()
        for e in extracted.edges:
            db.add(
                KnowledgeEdge(
                    doc_id=doc_id,
                    source_node_id=nodes[e.source].id,
                    target_node_id=nodes[e.target].id,
                    relation_type=e.relation_type,
                    description=e.description,
                )
            )
        doc.parse_status = "completed"
        doc.chunk_count = len(chunks)
        doc.structured_summary = extracted.structured_summary
        db.commit()
    except Exception as exc:
        db.rollback()
        doc = db.get(Document, doc_id)
        if doc:
            doc.parse_status = "failed"
            doc.error_message = (
                str(exc)[:240]
                if isinstance(exc, (ValueError, UnicodeError))
                else "解析或索引失败，请检查文件和服务配置"
            )
            db.commit()
        rag_store.remove(doc_id)
        raise ValueError(doc.error_message if doc else "文档已删除") from exc
    return public(doc)


def search_rag(doc_id, query, top_k=4):
    return rag_store.search(doc_id, query, top_k)


def get_memory_context(db, doc_id, session_id=None, *, concept=None, query=None):
    def rows(stmt):
        return [public(x) for x in db.scalars(stmt)]

    if session_id is not None:
        session = db.get(Session, session_id)
        if session is None or session.doc_id != doc_id:
            raise ValueError("会话不属于当前资料")
    weaknesses = (
        select(WeaknessLog)
        .join(Session)
        .where(Session.doc_id == doc_id, WeaknessLog.is_resolved.is_(False))
    )
    if session_id is not None:
        weaknesses = weaknesses.where(WeaknessLog.session_id == session_id)
    context = {
        "nodes": rows(
            select(KnowledgeNode).where(KnowledgeNode.doc_id == doc_id).order_by(KnowledgeNode.id)
        ),
        "edges": rows(
            select(KnowledgeEdge).where(KnowledgeEdge.doc_id == doc_id).order_by(KnowledgeEdge.id)
        ),
        "weaknesses": rows(weaknesses.order_by(WeaknessLog.id)),
    }

    if concept is not None or query:

        def compact(value):
            return "".join(rag_store.normalize(str(value)).split())

        needle = compact(query or "")
        # Match the longest concept at each position: “梯度下降” alone should
        # preserve “梯度” as a prerequisite instead of a second focus node.
        names = sorted(
            {compact(n["concept_name"]) for n in context["nodes"]}, key=len, reverse=True
        )
        matched_names = (
            set(re.findall("|".join(map(re.escape, names)), needle)) if names and needle else set()
        )
        focus = {
            n["id"]
            for n in context["nodes"]
            if (
                concept is not None
                and (concept == n["id"] or compact(concept) == compact(n["concept_name"]))
            )
            or compact(n["concept_name"]) in matched_names
        }
        parents = defaultdict(set)
        for edge in context["edges"]:
            if edge["relation_type"] == "prerequisite":
                parents[edge["target_node_id"]].add(edge["source_node_id"])
        seen = set(focus)
        frontier = list(focus)
        while frontier:
            fresh = parents[frontier.pop()] - seen
            seen.update(fresh)
            frontier.extend(fresh)
        context["matched_node_ids"] = sorted(focus)
        parent_ids = seen - focus
        context["prerequisites"] = [n for n in context["nodes"] if n["id"] in parent_ids]
        context["historical_weaknesses"] = rows(
            select(WeaknessLog)
            .join(Session)
            .where(Session.doc_id == doc_id, WeaknessLog.node_id.in_(seen))
            .order_by(WeaknessLog.id.desc())
        )
    return context


def update_mastery(db, node_id, delta):
    score = func.min(100, func.max(0, KnowledgeNode.mastery_score + int(delta)))
    result = db.execute(
        update(KnowledgeNode)
        .where(KnowledgeNode.id == node_id)
        .values(
            mastery_score=score,
            status=case((score < 40, "weak"), (score < 80, "learning"), else_="mastered"),
        )
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        raise ValueError("知识点不存在")
    node = db.get(KnowledgeNode, node_id, populate_existing=True)
    return public(node)


def log_weakness(db, session_id, node_id, weakness_type, detail):
    node = db.get(KnowledgeNode, node_id)
    session = db.get(Session, session_id)
    if node is None or session is None or node.doc_id != session.doc_id:
        raise ValueError("知识点与会话不匹配")
    if weakness_type == "need_lookup":
        db.execute(
            update(KnowledgeNode)
            .where(KnowledgeNode.id == node_id)
            .values(lookup_count=KnowledgeNode.lookup_count + 1)
        )
    row = WeaknessLog(
        session_id=session_id, node_id=node_id, weakness_type=weakness_type, detail=detail
    )
    db.add(row)
    db.flush()
    return public(row)


def export_memory_json(db, doc_id):
    ctx = get_memory_context(db, doc_id)
    return {
        "核心概念树": ctx["nodes"],
        "概念关联说明": ctx["edges"],
        "已掌握清单": [n for n in ctx["nodes"] if n["status"] == "mastered"],
        "薄弱点警示": {
            "知识点": [n for n in ctx["nodes"] if n["status"] == "weak"],
            "未解决记录": ctx["weaknesses"],
        },
    }


def export_memory_markdown(db, doc_id):
    data = export_memory_json(db, doc_id)
    nodes = data["核心概念树"]
    names = {n["id"]: n["concept_name"] for n in nodes}
    warnings = data["薄弱点警示"]
    lists = [
        [
            f"{n['concept_name']}：{n['summary']}（要点：{'；'.join(n['key_points']) or '暂无'}；笔记：{n['user_note'] or '无'}）"
            for n in nodes
        ],
        [
            f"{names[e['source_node_id']]} → {names[e['target_node_id']]}（{e['relation_type']}）：{e['description']}"
            for e in data["概念关联说明"]
        ],
        [f"{n['concept_name']}（{n['mastery_score']} 分）" for n in data["已掌握清单"]],
        [n["concept_name"] for n in warnings["知识点"]]
        + [w["detail"] for w in warnings["未解决记录"]],
    ]
    return (
        "\n\n".join(
            "## " + title + "\n\n" + "\n".join("- " + item for item in (items or ["暂无"]))
            for title, items in zip(data, lists)
        )
        + "\n"
    )


def _finish_delete(marker, doc_id, filename):
    rag_store.remove(doc_id)
    (settings.data_dir / "uploads" / Path(filename).name).unlink(missing_ok=True)
    marker.unlink(missing_ok=True)


def recover_deletions(db):
    """Complete interrupted cross-store cleanup before accepting requests."""
    for marker in settings.data_dir.glob(".delete-*.pending"):
        doc_id = int(marker.name.split("-")[1].split(".")[0])
        if db.get(Document, doc_id) is None:
            _finish_delete(marker, doc_id, marker.read_text(encoding="utf-8"))
        else:
            marker.unlink()


def delete_document(db, doc):
    doc_id, filename = doc.id, Path(doc.file_path).name
    marker = settings.data_dir / f".delete-{doc_id}.pending"
    with NamedTemporaryFile(
        dir=settings.data_dir, mode="w", encoding="utf-8", delete=False
    ) as temp:
        temp.write(filename)
    Path(temp.name).replace(marker)
    db.delete(doc)
    db.commit()
    _finish_delete(marker, doc_id, filename)
