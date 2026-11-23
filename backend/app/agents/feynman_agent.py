from collections.abc import Iterator
from app.agents.contracts import AgentInput, AgentEvent, MemoryUpdate, NodeUpdate, tokens, relevant
from app.services import memory_service as memory


def stream(db, turn: AgentInput) -> Iterator[AgentEvent]:
    # TODO(Agent 队友): replace baseline evidence heuristic with LangGraph assessment.
    nodes = memory.get_memory_context(db, turn.doc_id, turn.session_id)["nodes"]
    if not nodes:
        yield from tokens("这份资料的知识点已被清理，请重新上传资料后再练习。")
        return
    node = relevant(nodes, turn.content)
    match = node["concept_name"] in turn.content
    delta = 8 if match else -3
    label = "提及核心概念" if match else "需补充概念"
    updated = memory.update_mastery(db, node["id"], delta)
    if not match:
        memory.log_weakness(
            db, turn.session_id, node["id"], "omission", "请补充：" + node["concept_name"]
        )
    question = {
        "child": "可以举个生活中的例子吗？",
        "peer": "你能举例说明它在这里怎样起作用吗？",
        "examiner": "请说明它成立的条件，并给出一个反例。",
    }[turn.persona_type]
    yield from tokens(f"我们再看一下「{node['concept_name']}」。{question}")
    actual = updated["mastery_score"] - node["mastery_score"]
    yield MemoryUpdate(
        updates=[
            NodeUpdate(
                node_id=node["id"],
                concept_name=node["concept_name"],
                mastery_score=updated["mastery_score"],
                delta=actual,
                status=updated["status"],
                tag=f"{label} {actual:+d}",
            )
        ]
    )
