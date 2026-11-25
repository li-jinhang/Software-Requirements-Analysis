"""Shared SQL + RAG services. Mutating agent methods flush; caller owns the transaction."""

import re
import json
import heapq
import logging
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from collections import defaultdict
from tempfile import NamedTemporaryFile
from app.core.config import settings
from sqlalchemy import select, update, func, case

from app.db.models import (
    Document,
    KnowledgeNode,
    KnowledgeEdge,
    Message,
    Session,
    WeaknessLog,
    public,
)
from app.services import cognitive, doc_parser, rag_store, llm_client


def ingest_document(db, doc_id, profile):
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
        extracted = llm_client.extract(text, doc.title, profile)
        rag_store.remove(doc_id)
        rag_store.index(doc_id, doc.title, chunks)
        cognitive.store_graph(db, doc_id, extracted)
        cognitive.from_nodes(db, doc_id)
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
    return rag_store.search(doc_id, query.strip(), top_k) if query.strip() else []


def _learning_path(db, doc_id, nodes, edges):
    by_id = {n["id"]: n for n in nodes}
    parents = {i: set() for i in by_id}
    for e in edges:
        src, dst = e["source_node_id"], e["target_node_id"]
        if e["relation_type"] == "prerequisite" and src in by_id and dst in by_id:
            parents[dst].add(src)
    weak = dict(
        db.execute(
            select(WeaknessLog.node_id, func.count())
            .join(Session)
            .where(Session.doc_id == doc_id, WeaknessLog.is_resolved.is_(False))
            .group_by(WeaknessLog.node_id)
        ).all()
    )

    def priority(i):
        n = by_id[i]
        return (n["mastery_score"] >= 80, -weak.get(i, 0), -n["importance"], n["mastery_score"], i)

    # Remove only edges on a proven cycle, retaining all inter-cycle dependencies.
    deps = {i: p.copy() for i, p in parents.items()}
    relaxed = set()
    while True:
        sorter = TopologicalSorter({i: sorted(p) for i, p in deps.items()})
        try:
            sorter.prepare()
            break
        except CycleError as exc:
            cycle = exc.args[1][:-1]
            first = min(cycle, key=priority)
            deps[first].remove(cycle[cycle.index(first) - 1])
            relaxed.add(first)
    ready = [priority(i) for i in sorter.get_ready()]
    heapq.heapify(ready)
    result = []
    while ready:
        i = heapq.heappop(ready)[-1]
        n = by_id[i]
        reasons = []
        if i in relaxed:
            reasons.append("前置关系成环，作为环内复讲起点")
        if weak.get(i):
            reasons.append(f"有 {weak[i]} 条未解决薄弱记录")
        if n["mastery_score"] < 80:
            reasons.append(
                "先巩固前置概念，再复讲本节" if parents[i] else "基础尚未掌握，建议优先讲解"
            )
        else:
            reasons.append("已掌握，可用新例子快速复核")
        result.append(
            {k: n[k] for k in ("concept_name", "mastery_score", "status")}
            | {
                "node_id": i,
                "prerequisites": [by_id[p]["concept_name"] for p in sorted(parents[i])],
                "reason": "；".join(reasons),
            }
        )
        sorter.done(i)
        for item in sorter.get_ready():
            heapq.heappush(ready, priority(item))
    return result


def compute_learning_path(db, doc_id):
    nodes = [
        public(n)
        for n in db.scalars(
            select(KnowledgeNode).where(KnowledgeNode.doc_id == doc_id).order_by(KnowledgeNode.id)
        )
    ]
    edges = [
        public(e) for e in db.scalars(select(KnowledgeEdge).where(KnowledgeEdge.doc_id == doc_id))
    ]
    return _learning_path(db, doc_id, nodes, edges)


def _recent_lookups(db, session_id, nodes):
    names = {n["id"]: n["concept_name"] for n in nodes}
    scope = select(Message).where(
        Message.session_id == session_id, Message.window_type == "encyclopedia"
    )
    questions = db.scalars(
        scope.where(Message.sender_role == "user").order_by(Message.id.desc()).limit(5)
    ).all()
    logs = list(
        db.scalars(
            select(WeaknessLog)
            .where(WeaknessLog.session_id == session_id, WeaknessLog.weakness_type == "need_lookup")
            .order_by(WeaknessLog.id.desc())
            .limit(5)
        )
    )
    replies = {}
    if questions:
        current = None
        for m in db.scalars(scope.where(Message.id >= questions[-1].id).order_by(Message.id)):
            if m.sender_role == "user":
                current = m.id
            elif m.sender_role == "assistant" and current is not None and not json.loads(m.meta_json).get("proactive"):
                replies[current] = m
                current = None
    records = []
    for q in questions:
        reply = replies.get(q.id)
        log = next((w for w in logs if w.detail == q.content), None)
        ids = list(json.loads(reply.related_node_ids)) if reply else []
        if log:
            ids.append(log.node_id)
            logs.remove(log)
        concepts = [names[i] for i in dict.fromkeys(ids) if i in names]
        # SQLite timestamps can tie. For paired lookups use the log sequence
        # as the tie breaker shared with log-only records, not an unrelated message ID.
        stamp = max(q.created_at, log.created_at) if log else q.created_at
        order = (log.id if log else 0, q.id)
        records.append(
            (
                stamp,
                order,
                {
                    "question": q.content,
                    "related_concepts": concepts
                    or [name for name in names.values() if name in q.content],
                    "summary": " ".join(reply.content.split())[:180] if reply else "",
                },
            )
        )
    for w in logs:
        records.append(
            (
                w.created_at,
                (w.id, 0),
                {
                    "question": w.detail,
                    "related_concepts": [names[w.node_id]] if w.node_id in names else [],
                    "summary": w.detail[:180],
                },
            )
        )
    records.sort(key=lambda item: item[:2], reverse=True)
    return [record for _, _, record in records[:5]]


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
    context["recommended_learning_path"] = _learning_path(
        db, doc_id, context["nodes"], context["edges"]
    )
    if session_id is not None:
        context["recent_encyclopedia_lookups"] = _recent_lookups(db, session_id, context["nodes"])

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
        "推荐学习与复讲路径": ctx["recommended_learning_path"],
        "Agent 认知文档": cognitive.document(db, doc_id),
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
    sections = [
        "## " + title + "\n\n" + "\n".join("- " + item for item in (items or ["暂无"]))
        for title, items in zip(data, lists)
    ]
    steps = [
        f"{i}. {n['concept_name']}（{n['mastery_score']} 分）"
        f" · 前置：{'、'.join(n['prerequisites']) or '无'}。{n['reason']}。"
        for i, n in enumerate(data["推荐学习与复讲路径"], 1)
    ]
    sections.append("## 推荐学习与复讲路径\n\n" + "\n".join(steps or ["暂无"]))
    rubric = data["Agent 认知文档"]
    sections.append(f"## Agent 认知文档\n\n{rubric['overview']}\n\n总体掌握度 {rubric['mastery_score']}%")
    for group in rubric["sections"]:
        lines = [f"### {group['title']}"]
        for item in group["items"]:
            lines.append(f"- [{cognitive.LABELS[item['status']]}] {item['statement']}\n"
                         f"  - 判定标准：{item['key_criteria']}")
            if item.get("next_review_at"):
                lines.append(f"  - 复习阶段 {item['review_stage']} · 复习 {item['review_count']} 次"
                             f" · 下次复习 {item['next_review_at']} UTC")
            if item["user_evidence"]:
                lines.append("  - 回答依据：" + item["user_evidence"])
            if item["issue_note"]:
                lines.append("  - 待纠正：" + item["issue_note"])
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def _finish_delete(marker, doc_id, filename):
    rag_store.remove(doc_id)
    if filename:
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
    delete_documents(db, [doc])


def _stage_delete(doc):
    doc_id, filename = doc.id, Path(doc.file_path).name
    marker = settings.data_dir / f".delete-{doc_id}.pending"
    with NamedTemporaryFile(
        dir=settings.data_dir, mode="w", encoding="utf-8", delete=False
    ) as temp:
        temp.write(filename)
    Path(temp.name).replace(marker)
    return marker, doc_id, filename


def delete_documents(db, docs):
    """Journal file and index cleanup, then commit the caller's changes once."""
    removals = [_stage_delete(doc) for doc in docs]
    for doc in docs:
        db.delete(doc)
    db.commit()
    for marker, doc_id, filename in removals:
        try:
            _finish_delete(marker, doc_id, filename)
        except Exception:
            # The SQL deletion is final. The existing journal will retry physical cleanup on startup.
            logging.getLogger(__name__).exception("资料 %s 的删除清理将在重启时重试", doc_id)
