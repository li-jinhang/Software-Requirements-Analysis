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
    concepts = {n["id"]: n["concept_name"] for n in nodes}
    revisit = list(dict.fromkeys(concepts[w["node_id"]] for w in issues if w["node_id"] in concepts))
    plan = [f"用自己的话解释「{name}」，再用一个具体例子说明它的适用条件。" for name in revisit]
    if not plan:
        plan = ["选一个核心概念，用两句话向初学者解释。", "换一个例子，说明哪些条件会改变结论。"]
    summary = (f"你在这次练习中提到了 {mentioned} 个知识点。" if mentioned else
               "下次讲解时，可以先说出知识点的名称，再用自己的话解释它。")
    summary += (f"建议从「{revisit[0]}」开始复讲，看看能否脱离课件举例。" if revisit else
                "试着把其中一个概念讲给新的听众，并请对方复述。")
    return Report(
        coverage_score=round(100 * mentioned / max(1, len(nodes))),
        accuracy_score=round(sum(n["mastery_score"] for n in nodes) / max(1, len(nodes))),
        clarity_score=round(100 * sum(len(m.content) >= 20 for m in turns) / max(1, len(turns))),
        logic_score=round(
            100
            * sum(any(w in m.content for w in ("因为", "因此", "所以", "如果")) for m in turns)
            / max(1, len(turns))
        ),
        overall_comment=summary,
        weakness_summary=[w["detail"] for w in issues],
        review_plan="\n".join(plan),
    )
