from collections.abc import Iterator
from app.agents.contracts import AgentInput, AgentEvent, Citation, relevant, tokens
from app.services import memory_service as memory


def stream(db, turn: AgentInput) -> Iterator[AgentEvent]:
    # TODO(Agent 队友): compose an evidence-grounded explanation with LangGraph.
    hits = memory.search_rag(turn.doc_id, turn.content)
    yield Citation(citations=hits)
    nodes = memory.get_memory_context(db, turn.doc_id, turn.session_id)["nodes"]
    if nodes:
        node = relevant(nodes, turn.content)
        memory.log_weakness(db, turn.session_id, node["id"], "need_lookup", turn.content)
    if hits:
        hit = hits[0]
        yield from tokens(
            f"可对照《{hit['doc_title']}》片段 #{hit['chunk_index']}：{hit['snippet']}\n请用自己的话复述这段材料。"
        )
    else:
        yield from tokens("当前资料中没有召回原文，请补充资料后再查证。")
