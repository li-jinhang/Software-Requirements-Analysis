"""Model boundary: two transport retries, one graph-level JSON repair."""

import json
import logging
import re
import time

from openai import APIError, OpenAI

from app.core.config import settings
from .prompts import ASSESS, FOLLOWUP, PERSONAS

log = logging.getLogger(__name__)


class TeachingModelError(RuntimeError):
    pass


def client():
    return OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        timeout=30,
        max_retries=0,
    )


def offline():
    if settings.mock_llm:
        return True
    if not settings.openai_api_key.get_secret_value():
        raise TeachingModelError("未配置模型密钥，请配置后重试，或开启 MOCK_LLM 本地演示。")
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


def request(messages):
    with client() as sdk:
        for attempt in range(3):
            try:
                return sdk.chat.completions.create(
                    model=settings.openai_model,
                    messages=messages,
                    response_format={"type": "json_object"},
                ).choices[0].message.content or ""
            except APIError as exc:
                if attempt == 2:
                    raise model_error(exc) from exc
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


def assess(state, schema, correction=False):
    if offline():
        return local_assessment(state)
    payload = {
        "node": state["node"], "content": state["content"],
        "prerequisites": state["memory"].get("prerequisites", []),
        "weaknesses": state["memory"].get("historical_weaknesses", [])[:20],
        "history": recent_history(state), "citations": state["citations"],
    }
    instructions = ASSESS + "\nSchema:" + json.dumps(schema, ensure_ascii=False)
    if correction:
        instructions += "\n上次结果未通过校验。检查字段、分数、node_id 和原文证据后完整重写。"
    return request([
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ])


def followup(state, persona):
    if offline():
        from .prompts import opening
        yield "（本地规则演示）" + opening(state["node"]["concept_name"], persona)
        return
    payload = {
        "node": state["node"], "content": state["content"],
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
    # Once text has reached the client, replaying a failed stream would duplicate it.
    emitted = False
    with client() as sdk:
        for attempt in range(3):
            try:
                response = sdk.chat.completions.create(
                    model=settings.openai_model, messages=messages, stream=True, max_tokens=220,
                )
                with response:
                    for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            emitted = True
                            yield chunk.choices[0].delta.content
                if not emitted:
                    raise TeachingModelError("教学模型未返回追问，请保留讲解内容后重试。")
                return
            except APIError as exc:
                if emitted or attempt == 2:
                    raise model_error(exc) from exc
                time.sleep(0.25 * 2**attempt)
