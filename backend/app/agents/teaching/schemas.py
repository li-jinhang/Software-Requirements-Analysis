from typing import Annotated, Literal, TypedDict
import operator

from pydantic import BaseModel, ConfigDict, Field


class Problem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weakness_type: Literal["omission", "misconception", "jargon"]
    detail: str = Field(min_length=1, max_length=600)


class ItemIssue(BaseModel):
    issue_type: Literal["wrong", "inaccurate", "vague"]
    user_claim: str = Field(min_length=1, max_length=900)
    detail: str = Field(min_length=1, max_length=350)


class ItemMark(BaseModel):
    item_id: int
    status: Literal["mastered", "partial", "wrong"]
    user_evidence: str = Field(min_length=1, max_length=1200)
    issues: list[ItemIssue]


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_marks: list[ItemMark] = Field(default_factory=list)
    node_id: int = Field(gt=0)
    student_evidence: str = Field(min_length=1, max_length=1200, pattern=r"\S")
    source_evidence: str = Field(min_length=1, max_length=1800, pattern=r"\S")
    accuracy: int = Field(ge=0, le=100)
    clarity: int = Field(ge=0, le=100)
    on_topic: bool = True
    needs_help: bool = False
    problems: list[Problem] = Field(default_factory=list, max_length=5)
    followup_focus: str = Field(min_length=1, max_length=400)


class TeachingState(TypedDict, total=False):
    trace: Annotated[list[str], operator.add]
    content: str
    kind: str
    node: dict | None
    cognitive_items: list[dict]
    current_item_id: int | None
    next_item: dict | None
    accepted_early: bool
    memory: dict
    history: list[dict]
    citations: list[dict]
    assessment: dict | None
    assessment_error: str
    repair_count: int
    high_quality: bool
    streak: int
    delta: int
    question_kind: str
    response: str
    memory_synced: bool
    updates: list[dict]
