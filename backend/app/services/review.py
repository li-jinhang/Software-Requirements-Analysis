"""Recall practice, Ebbinghaus spacing, source traceability, and lightweight AI checks."""

from datetime import timedelta
import json
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update

from app.db.models import CognitiveItem, Document, KnowledgeNode, now, public
from app.services import rag_store
from app.services.model_connection import ModelConnection
from app.services.model_profiles import ModelError, resolve


INTERVAL_DAYS = (1, 3, 7, 15, 15)


class _ChallengePayload(BaseModel):
    challenge: str = Field(min_length=1, max_length=300)
    hint: str = Field(min_length=1, max_length=200)


class _VerifyPayload(BaseModel):
    verdict: Literal["pass", "partial", "fail"]
    score_hint: Literal["remembered", "again"]
    feedback: str = Field(min_length=1, max_length=600)
    missing_points: list[str] = Field(default_factory=list)
    matched_criteria: list[str] = Field(default_factory=list)


def eligible(doc_id):
    mastered_nodes = select(KnowledgeNode.id).where(
        KnowledgeNode.doc_id == doc_id, KnowledgeNode.mastery_score >= 80,
    ).correlate(None)
    return and_(
        CognitiveItem.doc_id == doc_id, CognitiveItem.status == "mastered",
        CognitiveItem.node_id.in_(mastered_nodes),
    )


def due_expression():
    return func.coalesce(CognitiveItem.next_review_at,
                         func.datetime(CognitiveItem.updated_at, f"+{INTERVAL_DAYS[0]} days"))


def _doc_sources(db, doc_id):
    owner = select(Document.user_id).where(
        Document.id == doc_id, Document.is_library.is_(True),
    ).correlate(None).scalar_subquery()
    sources = db.scalars(select(Document.id).where(
        Document.library_id == doc_id, Document.user_id == owner,
        Document.parse_status == "completed",
    )).all()
    where = {"doc_id": {"$in": [doc_id, *sources]}} if sources else {"doc_id": doc_id}
    try:
        has_chunks = bool(rag_store.collection().get(where=where, limit=1, include=[])["ids"])
    except Exception:
        has_chunks = False
    return sources, has_chunks


def _card_snippets(doc_id, sources, has_chunks, item, concept_name="", limit=2):
    if not has_chunks:
        return []
    query = " ".join(part for part in (concept_name or item.section_title, item.statement[:100]) if part).strip()
    if not query:
        return []
    try:
        hits = rag_store.search(doc_id, query, limit, source_ids=sources)
    except Exception:
        return []
    return [
        {
            "doc_title": h.get("doc_title", ""),
            "chunk_index": int(h.get("chunk_index", 0)),
            "snippet": str(h.get("snippet", ""))[:280],
        }
        for h in hits[:limit]
    ]


def _heuristic_hint(item, concept_name=""):
    parts = []
    if item.key_criteria.strip():
        parts.append(f"核验判据：{item.key_criteria.strip()}")
    if item.issue_note.strip():
        parts.append(f"历史盲区提醒：留意「{item.issue_note.strip()}」")
    if not parts:
        label = concept_name or item.section_title or "当前知识点"
        parts.append(f"尝试用自己的话说明「{label}」的核心机制、适用边界与因果关系。")
    return "；".join(parts)


def lists(db, doc_id):
    when = now()
    sources, has_chunks = _doc_sources(db, doc_id)
    cards = []
    rows = db.execute(
        select(
            CognitiveItem,
            KnowledgeNode.mastery_score,
            KnowledgeNode.concept_name,
            KnowledgeNode.user_note,
        )
        .join(KnowledgeNode, CognitiveItem.node_id == KnowledgeNode.id)
        .where(eligible(doc_id))
        .execution_options(populate_existing=True)
    )
    for item, mastery, concept_name, user_note in rows:
        card = public(item)
        card["mastery_score"] = mastery
        card["concept_name"] = concept_name or item.section_title
        card["user_note"] = user_note or ""
        card["next_remembered_days"] = next_interval(item.review_stage, "remembered")[1].days
        card["source_snippets"] = _card_snippets(doc_id, sources, has_chunks, item, concept_name)
        card["next_review_at"] = item.next_review_at or item.updated_at + timedelta(days=INTERVAL_DAYS[0])
        cards.append(card)
    cards.sort(key=lambda card: (card["next_review_at"], card["item_order"]))
    return {
        "due_items": [card for card in cards if card["next_review_at"] <= when],
        "all_reviewable_items": cards,
    }


def next_interval(stage, outcome):
    if outcome == "again":
        return 0, timedelta(minutes=10)
    stage = min(len(INTERVAL_DAYS), stage + 1)
    return stage, timedelta(days=INTERVAL_DAYS[stage - 1])


def record(db, item, outcome, observed_count):
    stage, interval = next_interval(item.review_stage, outcome)
    when = now()
    # A retried answer carries the same count and cannot advance the schedule twice.
    db.execute(update(CognitiveItem).where(
        CognitiveItem.id == item.id, eligible(item.doc_id), CognitiveItem.review_count == observed_count,
    ).values(review_stage=stage, review_count=CognitiveItem.review_count + 1,
             last_reviewed_at=when, next_review_at=when + interval, updated_at=CognitiveItem.updated_at)
      .execution_options(synchronize_session=False))
    db.commit()


def skip(db, item):
    when = now()
    db.execute(update(CognitiveItem).where(
        CognitiveItem.id == item.id, eligible(item.doc_id), due_expression() <= when,
    ).values(skip_count=CognitiveItem.skip_count + 1, next_review_at=when + timedelta(days=1),
             updated_at=CognitiveItem.updated_at).execution_options(synchronize_session=False))
    db.commit()


def advance(db, doc_id, days):
    db.execute(update(CognitiveItem).where(eligible(doc_id)).values(
        next_review_at=func.strftime("%Y-%m-%d %H:%M:%f", due_expression(), f"-{days} days"),
        updated_at=CognitiveItem.updated_at,
    ).execution_options(synchronize_session=False))
    db.commit()


def _review_profile():
    return resolve("feynman") or resolve("encyclopedia")


def _card_context(db, item):
    node = db.get(KnowledgeNode, item.node_id) if item.node_id else None
    concept_name = node.concept_name if node else item.section_title
    sources, has_chunks = _doc_sources(db, item.doc_id)
    snippets = _card_snippets(item.doc_id, sources, has_chunks, item, concept_name)
    return node, concept_name, snippets


def challenge_card(db, item):
    node, concept_name, snippets = _card_context(db, item)
    fallback_hint = _heuristic_hint(item, concept_name)
    profile = _review_profile()
    if profile is None:
        return {
            "available": False,
            "challenge": "",
            "hint": fallback_hint,
            "notice": "尚未配置教学模型，已为你提炼本地回忆线索；如需 AI 变式情景追问，请在设置中配置 feynman 或 encyclopedia 模型。",
        }
    instructions = (
        "你是费曼考官。请结合知识点、核验判据、历史盲区与课程原文，出一条 50~80 字左右的「变式应用场景追问」（challenge）"
        "以及一条简洁的「回忆线索」（hint）。不要直接照搬或泄露标准表述答案，用具体情境启发学习者主动回忆。"
    )
    material = json.dumps({
        "concept_name": concept_name,
        "section_title": item.section_title,
        "statement": item.statement,
        "key_criteria": item.key_criteria,
        "issue_note": item.issue_note,
        "user_note": node.user_note if node else "",
        "source_snippets": [s["snippet"] for s in snippets],
    }, ensure_ascii=False)
    try:
        out = ModelConnection(profile).structured(_ChallengePayload, instructions, material)
    except ModelError as exc:
        return {
            "available": False,
            "challenge": "",
            "hint": fallback_hint,
            "notice": f"AI 出题暂不可用（{exc}），已切换为本地回忆线索。",
        }
    return {
        "available": True,
        "challenge": out.challenge.strip(),
        "hint": out.hint.strip(),
        "notice": "",
    }


def verify_answer(db, item, answer):
    node, concept_name, snippets = _card_context(db, item)
    profile = _review_profile()
    if profile is None:
        return {
            "available": False,
            "verdict": "partial",
            "score_hint": "again",
            "feedback": "尚未配置教学模型，暂无法进行 AI 一句话核验。你可以直接点击「查看答案」对照标准表述与核验判据自评。",
            "missing_points": [],
            "matched_criteria": [],
            "notice": "尚未配置 feynman 或 encyclopedia 模型，已保留手动核对路径。",
        }
    instructions = (
        "你是严格的费曼学习核验官。请对照标准核心表述、核验判据与原文依据，评估学习者的一句话复述。"
        "若核心逻辑和关键判据均准确覆盖，verdict 为 pass、score_hint 为 remembered；"
        "若遗漏重要条件或表述模糊，verdict 为 partial、score_hint 为 again；"
        "若核心方向错误或严重偏离，verdict 为 fail、score_hint 为 again。"
        "用中文给出精炼点评（feedback）、已命中要点列表（matched_criteria）与遗漏或偏差点（missing_points）。"
    )
    material = json.dumps({
        "concept_name": concept_name,
        "section_title": item.section_title,
        "statement": item.statement,
        "key_criteria": item.key_criteria,
        "issue_note": item.issue_note,
        "user_note": node.user_note if node else "",
        "source_snippets": [s["snippet"] for s in snippets],
        "learner_answer": answer,
    }, ensure_ascii=False)
    try:
        out = ModelConnection(profile).structured(_VerifyPayload, instructions, material)
    except ModelError as exc:
        return {
            "available": False,
            "verdict": "partial",
            "score_hint": "again",
            "feedback": f"AI 核验暂不可用（{exc}）。请直接点击「查看答案」核对标准表述与判据要点。",
            "missing_points": [],
            "matched_criteria": [],
            "notice": str(exc),
        }
    return {
        "available": True,
        "verdict": out.verdict,
        "score_hint": out.score_hint,
        "feedback": out.feedback.strip(),
        "missing_points": [p.strip() for p in out.missing_points if p.strip()],
        "matched_criteria": [p.strip() for p in out.matched_criteria if p.strip()],
        "notice": "",
    }
