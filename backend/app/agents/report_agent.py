from sqlalchemy import select
from app.agents.contracts import Report
from app.db.models import Message
from app.services.memory_service import get_memory_context


def generate(db, session) -> Report:
    # TODO(Agent 队友): replace these transparent practice indicators with rubric scoring.
    ctx = get_memory_context(db, session.doc_id, session.id)
    nodes = ctx["nodes"]
    turns = db.scalars(
        select(Message).where(Message.session_id == session.id, Message.sender_role == "user")
    ).all()
    mentioned = sum(any(n["concept_name"] in m.content for m in turns) for n in nodes)
    issues = ctx["weaknesses"]
    return Report(
        coverage_score=round(100 * mentioned / max(1, len(nodes))),
        accuracy_score=round(sum(n["mastery_score"] for n in nodes) / max(1, len(nodes))),
        clarity_score=round(100 * sum(len(m.content) >= 20 for m in turns) / max(1, len(turns))),
        logic_score=round(
            100
            * sum(any(w in m.content for w in ("因为", "因此", "所以", "如果")) for m in turns)
            / max(1, len(turns))
        ),
        overall_comment=f"基线练习指标：共 {len(turns)} 次输入，提及 {mentioned} 个知识点，待复习记录 {len(issues)} 条。",
        weakness_summary=[w["detail"] for w in issues],
        review_plan="；".join(dict.fromkeys(w["detail"] for w in issues))
        or "选择一个核心概念，举例并解释其适用条件。",
    )
