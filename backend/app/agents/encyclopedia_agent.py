from collections.abc import Iterator
import json
import logging
import re

from openai import APIError
from pydantic import BaseModel, Field
from app.agents.contracts import AgentInput, AgentEvent, Citation, relevant, tokens
from app.core.config import settings
from app.services.llm_client import live_client, structured
from app.services.rag_store import lexical_terms
from app.services import memory_service as memory


class Explanation(BaseModel):
    source: str = Field(min_length=1)
    example: str = Field(min_length=1)
    retell: str = Field(min_length=1)


def offline_example(question):
    if re.search(r"交叉熵|cross.entropy", question, re.I):
        return r"分类时，真实类别的预测概率为 $p$，单样本交叉熵为 $-\ln p$。预测概率从 0.8 降到 0.2，损失就从约 0.223 增到 1.609。"
    if re.search(r"学习率|梯度下降|步长|震荡", question):
        return r"把损失曲面想成山谷。取 $L(w)=w^2$，梯度为 $2w$，从 $w=1$ 出发。学习率取 0.75 时依次走到 −0.5、0.25，左右摆动却逐渐靠近谷底；取 1.1 时走到 −1.2、1.44，摆动反而放大。震荡和发散要分开判断。"
    if re.search(r"损失|均方|MSE", question, re.I):
        return r"回归常用均方误差 $\mathrm{MSE}=\frac1n\sum_i(\hat y_i-y_i)^2$。真实值为 2、4，预测值为 3、2，两个误差分别为 1、−2，均方误差就是 $(1+4)/2=2.5$。平方可避免正负误差相互抵消。"
    return "可以做一组对照：先描述一个满足定义的情形，再只改变一个关键条件，检查结论是否还成立。注意区分必要条件与充分条件。"


def prepare(doc_id, question):
    hits = memory.search_rag(doc_id, question)
    answer = None
    if not settings.mock_llm and settings.openai_api_key.get_secret_value():
        try:
            evidence = [{"片段": h["chunk_index"], "原文": h["snippet"]} for h in hits]
            with live_client() as client:
                answer = structured(client, Explanation,
                    "用中文回答课程查证问题。source 仅概括所给课件并用 [片段 #编号] 溯源；"
                    "无片段时说明未检索到课件。example 独立运用可靠的学科知识满足学生请求，"
                    "即使课件未给公式或例子仍须自己构造，不能拒绝。损失函数举例优先用 MSE，"
                    "学习率问题用二次函数演算至少两步。需要类比时指出对应关系与局限；"
                    "公式用 $...$ 或 $$...$$，给出符号、代入与结果，不要在补充中虚构引用。"
                    "retell 用一句具体问题引导学生回左窗复述。各字段直接写正文。",
                    json.dumps({"问题": question, "课程片段": evidence}, ensure_ascii=False),
                )
        except (APIError, ValueError, IndexError) as exc:
            logging.getLogger(__name__).warning("百科生成失败 (%s)，返回原句", type(exc).__name__)
    if answer is None:
        terms = set(lexical_terms(question))
        candidates = ((s.strip(), h) for h in hits
                      for s in re.split(r"(?<=[。！？])|\n+", h["snippet"])
                      if s.strip() and not s.lstrip().startswith("#"))
        best = max(candidates, key=lambda pair: len(terms.intersection(lexical_terms(pair[0]))),
                   default=None)
        source = (f"{best[0]} [片段 #{best[1]['chunk_index']}]" if best
                  else "本次没有检索到可直接引用的课件正文。")
        answer = Explanation(source=source, example=offline_example(question),
                             retell="回到左窗，用自己的话解释例子中哪个条件改变了结果。")
    source = answer.source if hits else "本次没有检索到可引用的课件片段。"
    return hits, (f"**课件溯源**\n\n{source}\n\n**学科知识补充与举例**\n\n"
                  f"以下示例为基于通用学科知识的拓展补充，供你理解并转述给左侧听众。\n\n"
                  f"{answer.example}\n\n{answer.retell}")


def stream(db, turn: AgentInput) -> Iterator[AgentEvent]:
    hits, answer = prepare(turn.doc_id, turn.content)
    yield Citation(citations=hits)
    nodes = memory.get_memory_context(db, turn.doc_id, turn.session_id)["nodes"]
    if nodes:
        node = relevant(nodes, turn.content)
        memory.log_weakness(db, turn.session_id, node["id"], "need_lookup", turn.content)
    yield from tokens(answer)
