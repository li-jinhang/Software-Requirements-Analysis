"""Model boundary: two transport retries, one graph-level JSON repair."""

import json
import logging
import re
import time
from typing import Literal
from pydantic import create_model

from app.core.config import settings
from app.services.model_connection import ModelConnection, AuthenticationRejected
from app.services.model_profiles import ModelError, require_profile
from .schemas import Assessment, ItemIssue, ItemMark
from .prompts import ASSESS, FOLLOWUP, PERSONAS

log = logging.getLogger(__name__)


class TeachingModelError(ModelError):
    pass


class StreamedText(str):
    """Complete text whose deltas were already sent through the graph writer."""


def offline():
    # Deterministic regression fixtures may replace this hook. Production always
    # uses the same locally managed connection as the rest of the application.
    return False


def model_error(exc):
    status = getattr(exc, "status_code", None)
    return TeachingModelError(
        f"教学模型调用失败（HTTP {status}），请检查模型配置后重试。"
        if status else "教学模型连接失败或超时，请保留讲解内容并重试。"
    )


def recent_history(state):
    # Keep conversation context bounded and avoid re-sending nested assessment
    # metadata (which already contains quotes from earlier messages).
    return [{"window_type": m["window_type"], "sender_role": m["sender_role"],
             "content": m["content"][:1200]} for m in state["history"][-12:]]


def request(messages, schema=Assessment, on_chunk=None):
    connection = ModelConnection(require_profile("feynman"))
    emitted = False

    def forward(delta):
        nonlocal emitted
        if delta:
            emitted = True
            on_chunk(delta)

    for attempt in range(3):
        try:
            return connection.text(
                messages[0]["content"], messages[-1]["content"], schema,
                forward if on_chunk else None,
            )
        except ModelError as exc:
            # Never replay a partially visible stream or retry rejected credentials.
            if emitted or attempt == 2 or isinstance(exc, AuthenticationRejected):
                raise TeachingModelError(str(exc)) from exc
            log.warning("教学模型调用重试 %d (%s)", attempt + 1, type(exc).__name__)
            time.sleep(0.25 * 2**attempt)


def local_assessment(state):
    """Explicitly labelled offline evidence heuristic, not an LLM evaluation."""
    node, text = state["node"], state["content"]
    snippet = state["citations"][0]["snippet"]
    terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]{2}", node["summary"].lower()))
    overlap = sum(term in text.lower() for term in terms) / max(1, len(terms))
    problems = []
    accuracy = 75 if overlap >= 0.25 else 40
    if overlap < 0.25:
        problems.append({"weakness_type": "omission", "detail": "请补充原文定义中的关键要点或前提。"})
    # Only the explicit live model path may certify two high-quality answers.
    return json.dumps({
        "node_id": node["id"], "student_evidence": text[:1200],
        "source_evidence": snippet[:1000], "accuracy": accuracy,
        "clarity": 70, "problems": problems,
        "followup_focus": "举例解释这一概念的适用条件，并考虑条件改变的情况。",
    }, ensure_ascii=False)


def quote_table(content):
    # IDs preserve exact student wording, including formula spacing and punctuation.
    sentences = re.findall(r"[^。！？\n]{1,600}[。！？]?|[。！？]", content)
    if len(sentences) > 40:
        sentences = [content[i:i + 400] for i in range(0, len(content), 400)]
    return list(dict.fromkeys(s.strip() for s in sentences if s.strip())) or [content]


def restore_quotes(value, quotes):
    if isinstance(value, list):
        for item in value:
            restore_quotes(item, quotes)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"student_evidence", "user_evidence", "user_claim"} and type(item) is int:
                if not 0 <= item < len(quotes):
                    raise ValueError("引用的学生原话编号无效")
                value[key] = quotes[item]
            else:
                restore_quotes(item, quotes)


def assess(state, schema, correction=False):
    if offline():
        return local_assessment(state)
    payload = {
        "node": state["node"], "content": state["content"],
        "current_item_id": state.get("current_item_id"),
        "cognitive_items": [{k: row[k] for k in ("id", "section_title", "statement", "key_criteria")}
                            for row in state.get("cognitive_items", [])],
        "prerequisites": state["memory"].get("prerequisites", []),
        "weaknesses": state["memory"].get("historical_weaknesses", [])[:20],
        "history": recent_history(state), "citations": state["citations"],
    }
    result_schema = Assessment
    quotes = []
    if state.get("cognitive_items"):
        item = state["next_item"]
        excerpts = [item["statement"], item["key_criteria"]]
        excerpts.extend(h["snippet"] for h in state["citations"])
        choices = tuple(dict.fromkeys(s.strip()[:450] for excerpt in excerpts
                                      for s in re.split(r"(?<=[。！？])|\n", excerpt)
                                      if s.strip() and not s.lstrip().startswith("#")))
        if choices:
            quotes = quote_table(state["content"])
            payload["student_quotes"] = dict(enumerate(quotes))
            quote_id = Literal[tuple(range(len(quotes)))]
            issue = create_model("GroundedIssue", __base__=ItemIssue, user_claim=(quote_id, ...))
            mark = create_model("GroundedMark", __base__=ItemMark, user_evidence=(quote_id, ...),
                                issues=(list[issue], ...))
            result_schema = create_model("Assessment", __base__=Assessment,
                node_id=(Literal[state["node"]["id"]], ...), source_evidence=(Literal[choices], ...),
                student_evidence=(quote_id, ...), item_marks=(list[mark], ...))
            schema = result_schema.model_json_schema()
    instructions = ASSESS + "\nSchema:" + json.dumps(schema, ensure_ascii=False)
    if quotes:
        instructions += "\nstudent_evidence、user_evidence、user_claim 均填 student_quotes 中对应原句的整数编号。服务端按编号还原原话。"
    if correction:
        instructions += "\n上次校验反馈：" + state["assessment_error"] + " 按反馈修正，重新返回完整 JSON。"
    raw = request([
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ], schema=result_schema)
    if not quotes:
        return raw
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.partition("\n")[2].removesuffix("```").strip()
    parsed = json.loads(raw)
    restore_quotes(parsed, quotes)
    return json.dumps(parsed, ensure_ascii=False)


def followup(state, persona):
    if offline():
        from .prompts import opening
        yield "（本地规则演示）" + opening(state["node"]["concept_name"], persona)
        return
    payload = {
        "node": state["node"], "content": state["content"],
        "next_item": state.get("next_item"),
        "assessment": state.get("assessment"),
        "assessment_error": state.get("assessment_error", ""),
        "history": recent_history(state),
        "weaknesses": state["memory"].get("weaknesses", [])[-20:],
        "citations": state.get("citations", []),
    }
    messages = [
        {"role": "system", "content": FOLLOWUP + "\n角色：" + PERSONAS[persona]},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]
    from langgraph.config import get_stream_writer
    from app.agents.contracts import Token
    writer = get_stream_writer()
    emitted = []

    def forward(delta):
        emitted.append(delta)
        writer(Token(delta=delta))

    text = request(messages, schema=None, on_chunk=forward)
    yield StreamedText(text) if emitted else text
