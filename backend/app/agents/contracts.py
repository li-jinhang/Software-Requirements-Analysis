from typing import Literal
from pydantic import BaseModel
from app.schemas import Persona


class AgentInput(BaseModel):
    session_id: int
    doc_id: int
    persona_type: Persona
    content: str


class Token(BaseModel):
    delta: str


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


class Done(BaseModel):
    message_id: int


class Report(BaseModel):
    coverage_score: int
    accuracy_score: int
    clarity_score: int
    logic_score: int
    overall_comment: str
    weakness_summary: list[str]
    review_plan: str


AgentEvent = Token | Citation | MemoryUpdate
EVENT_NAMES = {Token: "token", Citation: "citation", MemoryUpdate: "memory_update", Done: "done"}


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
