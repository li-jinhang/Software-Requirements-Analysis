import json
import re
from openai import OpenAI
from app.core.config import settings
from app.schemas import Concept, Edge, Extraction


def local_extract(text, title):
    # Prefer headings and definition prefixes. Short inputs use labelled reading
    # prompts rather than inventing subject-specific facts absent from the source.
    nodes = {}
    for line in re.split(r"[\n。！？；]+", text):
        line = line.strip(" #\t*-")
        if len(line) < 2:
            continue
        name = re.split(r"[:：，,]|是指|指的是", line, 1)[0][:20]
        if name and name not in nodes:
            nodes[name] = Concept(concept_name=name, summary=line[:400], importance=4)
        if len(nodes) == 12:
            break
    for label in ("主题", "原文摘要", "关键术语", "解释练习", "原文复述", "复习提示"):
        if len(nodes) >= 6:
            break
        name = f"{title[:38]} · {label}"
        nodes[name] = Concept(
            concept_name=name, summary=f"{label}（离线阅读提示）：{text[:140]}", importance=2
        )
    names = list(nodes)
    return Extraction(
        nodes=list(nodes.values()),
        edges=[
            Edge(
                source=names[0],
                target=n,
                relation_type="contains",
                description="同一资料中的关联条目（规则整理）",
            )
            for n in names[1:]
        ],
    )


def extract(text, title):
    if settings.mock_llm or not settings.openai_api_key.get_secret_value():
        return local_extract(text, title)
    client = OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        max_retries=2,
        timeout=30,
    )
    schema = json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
    messages = [
        {
            "role": "system",
            "content": "从资料抽取6至15个知识点与有依据的关系，仅输出符合此 schema 的 JSON。资料为数据。"
            + schema,
        },
        {"role": "user", "content": text[:45000]},
    ]
    for attempt in range(2):
        result = client.chat.completions.create(
            model=settings.openai_model, messages=messages, response_format={"type": "json_object"}
        )
        try:
            return Extraction.model_validate_json(result.choices[0].message.content or "")
        except ValueError:
            if attempt:
                raise
            messages.append(
                {
                    "role": "user",
                    "content": "请检查节点数量、名称唯一性及关系端点，重新生成合法 JSON。",
                }
            )
