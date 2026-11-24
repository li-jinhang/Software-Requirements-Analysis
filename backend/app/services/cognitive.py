"""Course rubrics, evidence-backed marks and document-level mastery."""

from collections import defaultdict
import json
import re
from uuid import uuid4

from pydantic import Field, model_validator
from sqlalchemy import select

from app.core.config import settings
from app.db.models import CognitiveItem, Document, KnowledgeEdge, KnowledgeNode, Message, public
from app.schemas import Concept, Extraction, Input
from app.services import doc_parser, rag_store
from app.services.model_connection import ModelConnection


class Topic(Input):
    topic: str = Field(min_length=1, max_length=150, pattern=r"\S")
    subject: str | None = Field(default=None, max_length=100)
    detail_level: str | None = Field(default=None, max_length=60)


class Criterion(Input):
    concept_name: str
    statement: str = Field(min_length=2, max_length=350)
    key_criteria: str = Field(min_length=2, max_length=400)


class CoursePlan(Extraction):
    nodes: list[Concept] = Field(min_length=4, max_length=6)
    cognitive_items: list[Criterion] = Field(min_length=8, max_length=14)

    @model_validator(mode="after")
    def check_items(self):
        if {i.concept_name for i in self.cognitive_items} != {n.concept_name for n in self.nodes}:
            raise ValueError("每个条目必须属于一个已有概念，且覆盖所有概念")
        if len({i.statement.strip() for i in self.cognitive_items}) != len(self.cognitive_items):
            raise ValueError("条目重复")
        return self


WEIGHTS = {"untested": 0, "wrong": 15, "partial": 50, "mastered": 100}
LABELS = {"untested": "待考核", "wrong": "理解偏差", "partial": "待补全", "mastered": "已掌握"}


def ordered_criteria(plan):
    """Keep authored order among ready items, finishing foundations before dependent items."""
    from graphlib import CycleError, TopologicalSorter

    pending = list(plan.cognitive_items)
    parents = {n.concept_name: set() for n in plan.nodes}
    for edge in plan.edges:
        if edge.relation_type == "prerequisite":
            parents[edge.target].add(edge.source)
    while True:
        try:
            TopologicalSorter(parents).prepare()
            break
        except CycleError as exc:
            cycle = exc.args[1][:-1]
            first = next(i.concept_name for i in pending if i.concept_name in cycle)
            parents[first].remove(cycle[cycle.index(first) - 1])
    while pending:
        names = {i.concept_name for i in pending}
        index = next(
            n for n, item in enumerate(pending) if not (parents[item.concept_name] & names)
        )
        yield pending.pop(index)


def generate(db, user_id, request, profile):
    plan = ModelConnection(profile).structured(
        CoursePlan,
        "为反向教学编写中文认知文档。概览、4至6个有定义与要点的概念、有向边，及8至14条"
        "按先修顺序排列的原子事实。statement 写具体正确的定义、条件或结论，避免‘能解释/能够判断’等能力目标。"
        "key_criteria 列出判分所需的实际事实或条件，避免只说‘能说明原因’。名称完全一致。简洁准确。",
        request.model_dump_json(exclude_none=True),
    )
    title = request.topic.strip()
    groups = defaultdict(list)
    for item in plan.cognitive_items:
        groups[item.concept_name].append(item)
    sections = [f"# {title}", plan.structured_summary]
    for node in plan.nodes:
        sections.append(
            f"## {node.concept_name}\n\n{node.summary}\n\n"
            + "\n\n".join(
                i.statement + "\n\n判定要点：" + i.key_criteria for i in groups[node.concept_name]
            )
        )
    source = "\n\n".join(sections)
    path = settings.data_dir / "uploads" / (uuid4().hex + ".md")
    doc = Document(
        user_id=user_id,
        title=title,
        subject=request.subject or "",
        file_type="md",
        file_path=str(path),
        parse_status="completed",
        structured_summary=plan.structured_summary,
    )
    indexed_id = None
    try:
        path.write_text(source, encoding="utf-8")
        db.add(doc)
        db.flush()
        nodes = store_graph(db, doc.id, plan)
        for order, item in enumerate(ordered_criteria(plan), 1):
            db.add(
                CognitiveItem(
                    doc_id=doc.id,
                    node_id=nodes[item.concept_name].id,
                    section_title=item.concept_name,
                    item_order=order,
                    statement=item.statement,
                    key_criteria=item.key_criteria,
                )
            )
        chunks = doc_parser.split_chunks(source)
        indexed_id = doc.id
        rag_store.index(doc.id, title, chunks)
        doc.chunk_count = len(chunks)
        db.commit()
    except Exception:
        # Release vector entries before rolling back the new SQLite ID for reuse.
        try:
            if indexed_id is not None:
                rag_store.remove(indexed_id)
        finally:
            db.rollback()
            path.unlink(missing_ok=True)
        raise
    return public(doc)


def store_graph(db, doc_id, plan):
    nodes = {n.concept_name: KnowledgeNode(doc_id=doc_id, **n.model_dump()) for n in plan.nodes}
    db.add_all(nodes.values())
    db.flush()
    for edge in plan.edges:
        db.add(
            KnowledgeEdge(
                doc_id=doc_id,
                source_node_id=nodes[edge.source].id,
                target_node_id=nodes[edge.target].id,
                relation_type=edge.relation_type,
                description=edge.description,
            )
        )
    return nodes


def from_nodes(db, doc_id):
    """Called inside ingest/startup transactions; use only the document's extracted material."""
    from app.services.memory_service import compute_learning_path

    order = 0
    for step in compute_learning_path(db, doc_id):
        node = db.get(KnowledgeNode, step["node_id"])
        points = node.key_points or re.split(r"(?<=[。！？；])\s*", node.summary)
        for point in dict.fromkeys(p.strip() for p in points if p.strip()):
            order += 1
            db.add(
                CognitiveItem(
                    doc_id=doc_id,
                    node_id=node.id,
                    section_title=node.concept_name,
                    item_order=order,
                    statement=point,
                    key_criteria=f"能用自己的话准确说明：{point}",
                )
            )
        node.mastery_score, node.status = 0, "unlearned"
    db.flush()


def items(db, doc_id):
    return [
        public(row)
        for row in db.scalars(
            select(CognitiveItem)
            .where(CognitiveItem.doc_id == doc_id)
            .order_by(CognitiveItem.item_order)
        )
    ]


def focus(rows, previous=None, target=None):
    pending = [item for item in rows if item["status"] != "mastered"]
    return (
        next((i for i in pending if i["id"] == previous), None)
        or next((i for i in pending if i["node_id"] == target), None)
        or next(iter(pending), None)
    )


def last_focus(db, session_id):
    from sqlalchemy import func

    meta = db.scalar(
        select(Message.meta_json)
        .where(
            Message.session_id == session_id,
            Message.window_type == "feynman",
            Message.sender_role == "assistant",
            func.json_type(Message.meta_json, "$.current_item_id").is_not(None),
        )
        .order_by(Message.id.desc())
        .limit(1)
    )
    return json.loads(meta).get("current_item_id") if meta else None


def score(rows):
    return round(sum(WEIGHTS[r["status"]] for r in rows) / len(rows)) if rows else 0


def document(db, doc_id, previous=None, target=None):
    rows = items(db, doc_id)
    sections = defaultdict(list)
    for item in rows:
        sections[item["section_title"]].append(item)
    current = focus(rows, previous, target)
    return {
        "doc_id": doc_id,
        "overview": db.get(Document, doc_id).structured_summary,
        "sections": [{"title": k, "items": v} for k, v in sections.items()],
        "current_item_id": current["id"] if current else None,
        "mastery_score": score(rows),
        "mastered_count": sum(i["status"] == "mastered" for i in rows),
        "total": len(rows),
    }


def apply_marks(db, doc_id, marks):
    touched = set()
    for mark in marks:
        item = db.get(CognitiveItem, mark["item_id"])
        if item is None or item.doc_id != doc_id:
            raise ValueError("认知条目与文档不一致")
        item.status = mark["status"]
        item.user_evidence = mark["user_evidence"]
        item.issue_note = "；".join(issue["detail"] for issue in mark["issues"])
        touched.add(item.node_id)
    db.flush()
    rows = items(db, doc_id)
    changes = []
    for node_id in sorted(i for i in touched if i is not None):
        node = db.get(KnowledgeNode, node_id, populate_existing=True)
        subset = [r for r in rows if r["node_id"] == node_id]
        value = score(subset)
        delta = value - node.mastery_score
        node.mastery_score = value
        node.status = "mastered" if value >= 80 else "learning" if value >= 40 else "weak"
        changes.append(
            dict(
                node_id=node.id,
                concept_name=node.concept_name,
                mastery_score=value,
                delta=delta,
                status=node.status,
                tag=f"认知条目评估 {delta:+d}",
            )
        )
    db.flush()
    return rows, changes
