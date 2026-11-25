"""Recall practice, Ebbinghaus spacing, source traceability, and Socratic review agent."""

from datetime import timedelta
import json
import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update

from app.db.models import CognitiveItem, Document, KnowledgeNode, now, public
from app.services import rag_store
from app.services.model_connection import ModelConnection
from app.services.model_profiles import ModelError, resolve


INTERVAL_DAYS = (1, 3, 7, 15, 15)


class _ChallengePayload(BaseModel):
    scenario_question: str = Field(min_length=1, max_length=320)
    hint: str = Field(min_length=1, max_length=200)


class _VerifyPayload(BaseModel):
    verdict: Literal["pass", "partial", "fail"]
    score_hint: Literal["remembered", "again"]
    feedback: str = Field(min_length=1, max_length=600)
    socratic_followup: str = Field(default="", max_length=300)
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


def _extract_subject(statement, fallback):
    """Extract only the topic subject from a statement without leaking its conclusion/answer."""
    clean = re.sub(r"\$\$?[\s\S]*?\$\$?", "", statement or "").strip()
    if not clean:
        return fallback or "这一核心概念"
    head = re.split(r"[。！？!?；;\n]", clean, maxsplit=1)[0].strip()
    parts = re.split(r"(?:通过|利用|沿|根据|由|是|指|能够|可以|决定|衡量|用于|使得|解释|控制|调整)", head, maxsplit=1)
    subject = parts[0].strip(" ，,：:")
    if 2 <= len(subject) <= 18 and subject != head:
        return subject
    return fallback or "这一知识点"


def _fallback_challenge(item, concept_name=""):
    """Rewrite a card into a zero-spoiler examiner scenario prompt & oblique hint when offline."""
    topic = (concept_name or item.section_title or "这一知识点").strip()
    subject = _extract_subject(item.statement, topic)
    templates = (
        f"如果要在实际场景中运用「{subject}」，你会如何说明它的核心工作机制与适用前提？",
        f"假设要向初学同学解释「{subject}」，为什么它要这样设计？若忽略关键条件会出现什么问题？",
        f"当系统处理涉及「{subject}」的任务时，核心因果链条是如何建立并保证效果的？",
    )
    question = templates[(item.item_order or 0) % len(templates)]
    if item.issue_note.strip():
        hint = f"回忆方向：聚焦「{subject}」的作用机制，并特别检查此前容易遗漏的前提或边界区别。"
    else:
        hint = f"回忆方向：从「{topic}」要解决的问题出发，按“机制动作 → 关键条件 → 实际效果”三步梳理。"
    return question, hint


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
    fallback_question, fallback_hint = _fallback_challenge(item, concept_name)
    profile = _review_profile()
    if profile is None:
        return {
            "available": False,
            "scenario_question": fallback_question,
            "challenge": fallback_question,
            "hint": fallback_hint,
            "notice": "尚未配置教学模型，已生成离线费曼考官提问。",
        }
    instructions = (
        "你是严格的费曼考官。请结合知识点、核验判据、历史盲区与课程原文，设计一道【零剧透】的实战应用场景追问（scenario_question，45~85字）"
        "以及一句【不剧透】的侧向回忆提示（hint，20~45字）。"
        "绝对禁止在 scenario_question 或 hint 中直接照搬、复述或泄露标准核心表述（statement）与核验判据（key_criteria）的答案结论，"
        "必须通过具体工程/问题情景启发学习者主动回忆因果与边界条件。"
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
            "scenario_question": fallback_question,
            "challenge": fallback_question,
            "hint": fallback_hint,
            "notice": f"AI 出题暂不可用（{exc}），已切换为离线费曼考官提问。",
        }
    question = out.scenario_question.strip()
    return {
        "available": True,
        "scenario_question": question,
        "challenge": question,
        "hint": out.hint.strip(),
        "notice": "",
    }


def verify_answer(db, item, answer, followup_answer=""):
    node, concept_name, snippets = _card_context(db, item)
    previous_evidence = (item.user_evidence or "").strip()
    primary = answer.strip()
    followup = followup_answer.strip()
    is_second_round = bool(followup)
    combined_answer = f"{primary}（补充：{followup}）" if is_second_round else primary

    profile = _review_profile()
    if profile is None:
        return {
            "available": False,
            "verdict": "partial",
            "score_hint": "again",
            "feedback": "尚未配置教学模型，暂无法进行 AI 核验。你可以直接点击「直接查看答案」对照标准表述与判据自评。",
            "socratic_followup": "",
            "missing_points": [],
            "matched_criteria": [],
            "previous_evidence": previous_evidence,
            "updated_evidence": "",
            "evidence_evolved": False,
            "notice": "尚未配置 feynman 或 encyclopedia 模型，已保留手动核对路径。",
        }

    instructions = (
        "你是费曼学习考官。请对照标准核心表述、核验判据与原文依据，评估学习者的口述回答（若提供 followup_answer，请将初次回答与补充回答合并作完整判定）。"
        "1) 核心机制与关键判据全部准确覆盖：verdict 为 pass，score_hint 为 remembered，socratic_followup 留空；"
        "2) 方向基本正确但遗漏重要条件或表述不够精确：verdict 为 partial，score_hint 为 again；"
        "若当前为首轮核验（is_second_round 为 false），必须在 socratic_followup 中给出 1 句精准的苏格拉底式追问，点出方向引导学习者补全遗漏条件，不要直接给出标准答案；"
        "3) 核心方向错误或答非所问：verdict 为 fail，score_hint 为 again，socratic_followup 留空。"
        "用中文给出精炼点评（feedback）、已命中要点（matched_criteria）与遗漏/偏差点（missing_points）。"
    )
    material = json.dumps({
        "concept_name": concept_name,
        "section_title": item.section_title,
        "statement": item.statement,
        "key_criteria": item.key_criteria,
        "issue_note": item.issue_note,
        "user_note": node.user_note if node else "",
        "source_snippets": [s["snippet"] for s in snippets],
        "learner_answer": primary,
        "followup_answer": followup,
        "is_second_round": is_second_round,
    }, ensure_ascii=False)
    try:
        out = ModelConnection(profile).structured(_VerifyPayload, instructions, material)
    except ModelError as exc:
        return {
            "available": False,
            "verdict": "partial",
            "score_hint": "again",
            "feedback": f"AI 核验暂不可用（{exc}）。请点击「直接查看答案」手动核对标准表述与判据要点。",
            "socratic_followup": "",
            "missing_points": [],
            "matched_criteria": [],
            "previous_evidence": previous_evidence,
            "updated_evidence": "",
            "evidence_evolved": False,
            "notice": str(exc),
        }

    missing = [p.strip() for p in out.missing_points if p.strip()]
    matched = [p.strip() for p in out.matched_criteria if p.strip()]
    socratic = out.socratic_followup.strip() if (out.verdict == "partial" and not is_second_round) else ""
    if out.verdict == "partial" and not is_second_round and not socratic:
        focus = missing[0] if missing else (concept_name or "关键前置条件")
        socratic = f"你已经抓住了主干方向，再进一步想一下：如果结合「{focus}」来约束这个过程，结果会有什么不同？"

    updated_evidence = ""
    evidence_evolved = False
    if out.verdict == "pass" and len(combined_answer) >= 2:
        updated_evidence = combined_answer
        evidence_evolved = True
        db.execute(
            update(CognitiveItem)
            .where(CognitiveItem.id == item.id, eligible(item.doc_id))
            .values(user_evidence=updated_evidence, updated_at=CognitiveItem.updated_at)
            .execution_options(synchronize_session=False)
        )
        db.commit()

    return {
        "available": True,
        "verdict": out.verdict,
        "score_hint": out.score_hint,
        "feedback": out.feedback.strip(),
        "socratic_followup": socratic,
        "missing_points": missing,
        "matched_criteria": matched,
        "previous_evidence": previous_evidence,
        "updated_evidence": updated_evidence,
        "evidence_evolved": evidence_evolved,
        "notice": "",
    }
