from typing import Literal
from pydantic import BaseModel, Field
from app.schemas import Persona
from app.schemas.responses import CognitiveItemOut


class AgentInput(BaseModel):
    session_id: int
    doc_id: int
    persona_type: Persona
    content: str
    target_node_id: int | None = None
    action: Literal["explain", "opening", "lesson"] = "explain"


class Token(BaseModel):
    delta: str


class Preview(BaseModel):
    text: str


class StreamError(BaseModel):
    detail: str


class CitationItem(BaseModel):
    doc_title: str
    chunk_index: int
    snippet: str
    score: float


class Citation(BaseModel):
    citations: list[CitationItem]


class NodeUpdate(BaseModel):
    node_id: int
    concept_name: str
    mastery_score: int
    delta: int
    status: Literal["weak", "learning", "mastered", "unlearned"]
    tag: str


class MemoryUpdate(BaseModel):
    updates: list[NodeUpdate]


class CognitiveUpdate(BaseModel):
    review_completed: bool = False
    items: list[CognitiveItemOut]
    current_item_id: int | None
    mastery_score: int
    mastered_count: int


class RubricReference(BaseModel):
    kind: Literal["cognitive_item"] = "cognitive_item"
    item_id: int
    section_title: str


class EncyclopediaPush(BaseModel):
    message_id: int
    proactive: Literal[True] = True
    issue_type: Literal["wrong", "inaccurate", "vague"]
    label: str
    item_id: int
    concept_name: str
    item_statement: str
    user_claim: str
    accurate_explanation: str
    citations: list[RubricReference | CitationItem]


class Done(BaseModel):
    message_id: int


class TurnMetadata(BaseModel):
    """Internal graph result for message persistence; never an SSE event."""

    metadata: dict = Field(default_factory=dict)


class Report(BaseModel):
    coverage_score: int = Field(ge=0, le=100)
    accuracy_score: int = Field(ge=0, le=100)
    clarity_score: int = Field(ge=0, le=100)
    logic_score: int = Field(ge=0, le=100)
    overall_comment: str
    weakness_summary: list[str]
    review_plan: str


AgentEvent = Token | Preview | Citation | MemoryUpdate | TurnMetadata | CognitiveUpdate | EncyclopediaPush
EVENT_NAMES = {
    CognitiveUpdate: "cognitive_update", EncyclopediaPush: "encyclopedia_push",
    Token: "token", Preview: "preview", Citation: "citation",
    MemoryUpdate: "memory_update", Done: "done", StreamError: "error",
}


def tokens(text):
    for i in range(0, len(text), 8):
        yield Token(delta=text[i : i + 8])


def relevant(nodes, text):
    return max(
        nodes,
        key=lambda n: (
            n["concept_name"] in text,
            len(set(n["concept_name"]) & set(text)),
            -n["mastery_score"],
        ),
    )
