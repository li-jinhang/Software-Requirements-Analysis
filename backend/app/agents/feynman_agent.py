from collections.abc import Iterator
from app.agents.contracts import AgentInput, AgentEvent
from app.agents.teaching.graph import TeachingRuntime, build_graph


def stream(db, turn: AgentInput) -> Iterator[AgentEvent]:
    """Keep the existing adapter contract; the graph owns teaching decisions."""
    yield from build_graph().stream(
        {"trace": []}, context=TeachingRuntime(db=db, turn=turn), stream_mode="custom",
    )
