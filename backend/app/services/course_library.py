"""Clarify a student's scope, then author a connected course curriculum."""

import logging
import unicodedata
from graphlib import TopologicalSorter
from typing import Annotated

from pydantic import ConfigDict, Field, model_validator
from sqlalchemy import update

from app.db.database import SessionLocal
from app.db.models import Document
from app.schemas import Concept, Extraction, Input
from app.services import cognitive
from app.services.model_connection import ModelConnection
from app.services.model_profiles import ModelError

log = logging.getLogger(__name__)


class LibraryName(Input):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)


class Clarification(Input):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=240)
    answer: str = Field(min_length=1, max_length=1500)


class CourseBrief(LibraryName):
    requirements: str = Field(default="", max_length=2000)


class CourseRequest(CourseBrief):
    answers: list[Clarification] = Field(default_factory=list, max_length=3)
    use_defaults: bool = False


class ScopeDecision(Input):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(default="", max_length=240)
    scope: str = Field(default="", max_length=800)

    @model_validator(mode="after")
    def exactly_one(self):
        if bool(self.question) == bool(self.scope):
            raise ValueError("只返回一个追问或可执行的课程范围")
        return self


class GenerationRequest(CourseRequest):
    scope: str = Field(min_length=1, max_length=800)


class SetupState(Input):
    request: CourseRequest
    question: str = ""
    scope: str = ""


class ContinueSetup(Input):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(default="", max_length=240)
    answer: str = Field(default="", max_length=1500)
    use_defaults: bool = False


class CurriculumConcept(Concept):
    summary: str = Field(min_length=1, max_length=140)
    key_points: list[Annotated[str, Field(min_length=2, max_length=140)]] = Field(min_length=1, max_length=2)


class CurriculumPlan(Extraction):
    structured_summary: str = Field(min_length=1, max_length=600)
    nodes: list[CurriculumConcept] = Field(min_length=12, max_length=20)

    @property
    def cognitive_items(self):
        return [
            cognitive.Criterion(concept_name=node.concept_name, statement=point,
                                key_criteria=f"用自己的话准确说明：{point}")
            for node in self.nodes for point in dict.fromkeys(node.key_points)
        ]

    @model_validator(mode="after")
    def check_curriculum(self):
        names = {n.concept_name for n in self.nodes}
        normalized = {unicodedata.normalize("NFKC", n).strip().casefold() for n in names}
        if "" in normalized or len(normalized) != len(names):
            raise ValueError("课程节点须有唯一的非空名称")
        relations = {(e.source, e.target, e.relation_type) for e in self.edges}
        if len(relations) != len(self.edges):
            raise ValueError("课程关系重复")
        containment = [(a, b) for a, b, rel in relations if rel == "contains"]
        modules = {a for a, _ in containment}
        children = [b for _, b in containment]
        if (
            not 3 <= len(modules) <= 6
            or set(children) != names - modules
            or len(children) != len(set(children))
            or any(sum(a == m for a, _ in containment) < 2 for m in modules)
        ):
            raise ValueError("需要 3–6 个模块，每个模块至少包含两个概念，每个概念只归属一个模块")
        dependencies = [(a, b) for a, b, rel in relations if rel == "prerequisite"]
        if not dependencies:
            raise ValueError("需要明确先修顺序")
        order = TopologicalSorter()
        for source, target in dependencies:
            order.add(target, source)
        order.prepare()
        neighbors = {name: set() for name in names}
        for source, target, _ in relations:
            neighbors[source].add(target)
            neighbors[target].add(source)
        seen = set()
        pending = [self.nodes[0].concept_name]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(neighbors[current] - seen)
        if seen != names:
            raise ValueError("模块间须有明确关系，将课程连成一张图谱")
        return self


def clarify(request, profile):
    final_round = request.use_defaults or len(request.answers) == 3
    instructions = (
        "判断学生要学习的课程内容和范围。学校、教材和进度都是可选信息。课程可识别且能从其"
        "名称和要求形成有用的通用课程框架时，直接返回 scope，注明实际学习目标、范围及必要假设。"
        "仅在课程有多种实质不同的含义、目标冲突或遗漏的信息会显著改变框架时，返回 question。"
        "只提出一个容易回答的具体问题，不能重复已回答的问题。不要要求上传任何资料。用简体中文。"
    )
    if final_round:
        instructions += "学生选择通用范围或已补充三轮。必须返回 scope，清楚写明必要假设，question 留空。"
    decision = ModelConnection(profile).structured(ScopeDecision, instructions, request.model_dump_json())
    if decision.question and final_round:
        return ScopeDecision(scope="按学生提供的课程名称和学习要求采用通用课程范围，注明重要假设。")
    return decision


def draft(request, profile):
    return ModelConnection(profile).structured(
        CurriculumPlan,
        "根据学生的信息和拟定的范围，编写简体中文课程框架。structured_summary 说明适用范围、模块顺序和"
        "所用假设。用 12–20 个节点覆盖范围，其中 3–6 个是课程模块，其余是具体知识。"
        "每个节点用一两句给出定义或概述，并列出一到两条不同的关键事实、适用条件或结论。"
        "每个模块用 contains 边指向至少两个知识点，每个知识点只归属一个模块。用有向 prerequisite"
        "标记真实的先修关系（先修 → 后续，避免成环），并可加入有依据的对比和应用关系。"
        "为不同模块选取有实际学习意义的关联，使图谱整体连通。"
        "模块要点体现模块内知识之间的关系。按先基础后应用的顺序编排。定义和关系解释要简洁准确。"
        "避免凭空引用教材、课件、院校大纲或考试范围。",
        request.model_dump_json(),
        on_chunk=lambda _: None,  # Keep large compatible API responses off the buffered path.
    )


def generate(db, user_id, request, profile):
    return cognitive.persist_plan(db, user_id, request.title, request.title, draft(request, profile), is_library=True)


def run_setup(doc_id, profile):
    """Continue a persisted setup during the application's lifetime."""
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if not doc or doc.parse_status != "processing" or not doc.library_setup:
            return
        try:
            state = SetupState.model_validate(doc.library_setup)
            db.rollback()  # Release the read transaction while waiting for inference.
            if not state.scope:
                decision = clarify(state.request, profile)
                state.question, state.scope = decision.question, decision.scope
                db.execute(update(Document).where(Document.id == doc_id).values(
                    library_setup=state.model_dump(),
                    parse_status="pending" if state.question else "processing",
                ))
                db.commit()
                if state.question:
                    return
            request = GenerationRequest(**state.request.model_dump(), scope=state.scope)
            plan = draft(request, profile)
            doc = db.get(Document, doc_id, populate_existing=True)
            if doc is not None and doc.parse_status == "processing":
                cognitive.persist_plan(
                    db, doc.user_id, doc.title, doc.subject, plan, is_library=True, document=doc
                )
        except Exception as exc:
            db.rollback()
            log.warning("Course library %s setup failed (%s)", doc_id, type(exc).__name__)
            message = str(exc) if isinstance(exc, ModelError) else "课程框架生成失败，请重试。"
            db.execute(update(Document).where(
                Document.id == doc_id, Document.parse_status == "processing",
            ).values(parse_status="failed", error_message=message[:240]))
            db.commit()
