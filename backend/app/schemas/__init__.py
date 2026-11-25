from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Persona = Literal["child", "peer", "examiner"]
Relation = Literal["prerequisite", "contains", "contrast", "applies_to"]
Weakness = Literal["omission", "misconception", "jargon", "need_lookup"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=64, pattern=r"^\S+$")
    password: str = Field(min_length=6, max_length=72)

    @field_validator("password")
    @classmethod
    def check_bytes(cls, v):
        if len(v.encode()) > 72:
            raise ValueError("密码至多 72 个 UTF-8 字节")
        return v


class Register(Login):
    nickname: str = Field(min_length=1, max_length=64)


class UserOut(BaseModel):
    id: int
    username: str
    nickname: str
    role: Literal["admin", "student"]


class AuthOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class PersonaChange(Input):
    persona_type: Persona


class TimeAdvance(Input):
    days: int = Field(gt=0, le=3650)


class SessionCreate(PersonaChange):
    mode: Literal["teach", "review", "guided"] = "teach"
    target_item_ids: list[int] = Field(default_factory=list)
    doc_id: int = Field(gt=0)
    title: str = Field(default="费曼练习", min_length=1, max_length=150)
    persona_type: Persona = "peer"
    node_id: int | None = Field(default=None, gt=0)


class Turn(Input):
    content: str = Field(min_length=1, max_length=8000, pattern=r"\S")


class Note(Input):
    user_note: str = Field(max_length=8000)


class Concept(Input):
    key_points: list[Annotated[str, Field(min_length=1, max_length=350)]] = Field(
        min_length=1, max_length=5
    )
    concept_name: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=1000)
    importance: int = Field(ge=1, le=5)


class Edge(Input):
    source: str
    target: str
    relation_type: Relation
    description: str = Field(min_length=1, max_length=500, pattern=r"[\u3400-\u9fff]")


class Extraction(Input):
    structured_summary: str = Field(min_length=1, max_length=1800)
    nodes: list[Concept] = Field(min_length=6, max_length=15)
    edges: list[Edge] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def validate_edges(self):
        names = {n.concept_name for n in self.nodes}
        if len(names) != len(self.nodes) or any(
            e.source not in names or e.target not in names or e.source == e.target
            for e in self.edges
        ):
            raise ValueError("节点名称或关系端点无效")
        return self
