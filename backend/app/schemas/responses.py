from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel
from app.schemas import Persona, Relation, Weakness


class DocumentOut(BaseModel):
    id: int
    user_id: int
    is_library: bool = False
    library_id: int | None = None
    library_setup: dict[str, Any] | None = None
    title: str
    subject: str
    file_type: str
    parse_status: Literal["pending", "processing", "completed", "failed"]
    error_message: str
    chunk_count: int
    structured_summary: str
    created_at: datetime


class CognitiveItemOut(BaseModel):
    review_stage: int = 0
    review_count: int = 0
    skip_count: int = 0
    next_review_at: datetime | None = None
    last_reviewed_at: datetime | None = None
    id: int
    doc_id: int
    node_id: int | None
    section_title: str
    item_order: int
    statement: str
    key_criteria: str
    status: Literal["untested", "partial", "wrong", "mastered"]
    user_evidence: str
    issue_note: str
    updated_at: datetime


class ReviewSnippetOut(BaseModel):
    doc_title: str = ""
    chunk_index: int = 0
    snippet: str


class ReviewCardOut(CognitiveItemOut):
    mastery_score: int
    next_review_at: datetime
    concept_name: str = ""
    user_note: str = ""
    next_remembered_days: int = 1
    source_snippets: list[ReviewSnippetOut] = []


class ReviewListsOut(BaseModel):
    due_items: list[ReviewCardOut]
    all_reviewable_items: list[ReviewCardOut]


class ReviewChallengeOut(BaseModel):
    available: bool = True
    challenge: str = ""
    hint: str = ""
    notice: str = ""


class ReviewVerifyOut(BaseModel):
    available: bool = True
    verdict: Literal["pass", "partial", "fail"]
    score_hint: Literal["remembered", "again"]
    feedback: str
    missing_points: list[str] = []
    matched_criteria: list[str] = []
    notice: str = ""


class CognitiveSection(BaseModel):
    title: str
    items: list[CognitiveItemOut]


class CognitiveDocOut(BaseModel):
    doc_id: int
    overview: str
    sections: list[CognitiveSection]
    current_item_id: int | None
    mastery_score: int
    mastered_count: int
    total: int


class NodeOut(BaseModel):
    id: int
    doc_id: int
    concept_name: str
    key_points: list[str]
    summary: str
    importance: int
    mastery_score: int
    status: Literal["unlearned", "weak", "learning", "mastered"]
    lookup_count: int
    user_note: str
    updated_at: datetime


class EdgeOut(BaseModel):
    id: int
    doc_id: int
    source_node_id: int
    target_node_id: int
    relation_type: Relation
    description: str


class GraphOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]


class WeaknessOut(BaseModel):
    id: int
    session_id: int
    node_id: int
    weakness_type: Weakness
    detail: str
    is_resolved: bool
    created_at: datetime


class LearningStep(BaseModel):
    node_id: int
    concept_name: str
    mastery_score: int
    status: str
    prerequisites: list[str]
    reason: str


class RecentLookup(BaseModel):
    question: str
    related_concepts: list[str]
    summary: str


class ContextOut(GraphOut):
    recommended_learning_path: list[LearningStep] = []
    recent_encyclopedia_lookups: list[RecentLookup] = []
    matched_node_ids: list[int] = []
    prerequisites: list[NodeOut] = []
    historical_weaknesses: list[WeaknessOut] = []
    weaknesses: list[WeaknessOut]


class SessionOut(BaseModel):
    mode: Literal["teach", "review", "assessment", "guided"] = "teach"
    target_item_ids: list[int] = []
    review_completed: bool = False
    id: int
    user_id: int
    doc_id: int
    title: str
    persona_type: Persona
    target_node_id: int | None = None
    status: Literal["active", "completed"]
    created_at: datetime
    ended_at: datetime | None


class MessageOut(BaseModel):
    id: int
    session_id: int
    window_type: Literal["feynman", "encyclopedia"]
    sender_role: Literal["user", "assistant", "system"]
    content: str
    related_node_ids: list[int]
    meta_json: dict[str, Any]
    created_at: datetime


class ReportOut(BaseModel):
    id: int
    session_id: int
    coverage_score: int
    accuracy_score: int
    clarity_score: int
    logic_score: int
    overall_comment: str
    weakness_summary: list[str]
    review_plan: str
    created_at: datetime


class RankOut(BaseModel):
    node_id: int
    concept_name: str
    count: int


class StatsOut(BaseModel):
    total_users: int
    total_docs: int
    total_nodes: int
    total_sessions: int
    avg_mastery: float
    top_weaknesses: list[RankOut]
