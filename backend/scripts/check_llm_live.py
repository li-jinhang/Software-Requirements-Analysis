"""Run a strict live-provider probe with only the bundled teaching sample."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def timed(label, action):
    start = perf_counter()
    try:
        return action()
    finally:
        print(f"{label}  {perf_counter()-start:.2f} s", flush=True)


def probe():
    from app.core.config import settings
    from app.services import doc_parser, llm_client, rag_store

    if not settings.openai_api_key.get_secret_value():
        print("未配置 OPENAI_API_KEY。请填写 backend/.env 中的 Key、Base URL 与模型名后重试。")
        return 2
    path = BACKEND / "data/sample_docs/操作系统：进程调度与死锁.md"
    text = doc_parser.parse(path)
    print(f"对话模型 {settings.openai_model}；检索模式 {settings.embedding_provider}")
    try:
        with llm_client.live_client() as sdk:
            result = timed(
                "真实对话",
                lambda: sdk.chat.completions.create(
                    model=settings.openai_model,
                    messages=[{"role": "user", "content": "请用一句中文解释进程调度。"}],
                ),
            )
            answer = result.choices[0].message.content
            if not answer or not answer.strip():
                raise ValueError("对话内容为空")
            print(answer[:160])
            graph = timed("结构化抽取", lambda: llm_client.live_extract(sdk, text, path.stem))
        print(
            f"{len(graph.nodes)} 个概念，{len(graph.edges)} 条关系。{graph.structured_summary[:180]}"
        )
        print("概念示例", graph.nodes[0].model_dump_json())
        print("关系示例", graph.edges[0].model_dump_json())
        timed(
            "索引写入",
            lambda: rag_store.index(
                1, path.stem, doc_parser.split_chunks(text), strict_remote=True
            ),
        )
        hits = timed(
            "混合检索", lambda: rag_store.search(1, "死锁的必要条件", 2, strict_remote=True)
        )
        if not hits:
            raise ValueError("未检索到切片")
        print(
            f"首条命中 #{hits[0]['chunk_index']}  分数 {hits[0]['score']}  {hits[0]['snippet'][:120]}"
        )
        return 0
    except Exception as exc:
        print(
            f"自检失败：{type(exc).__name__}，HTTP {getattr(exc,'status_code',None) or '—'}。"
            "请检查网络、模型名、端点、权限以及结构化输出支持。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    # Release Chroma's native file handles before removing its disposable directory on Windows.
    with tempfile.TemporaryDirectory(prefix="feynman-live-") as tmp:
        result = subprocess.run(
            [sys.executable, "-c", "from check_llm_live import probe; raise SystemExit(probe())"],
            cwd=Path(__file__).resolve().parent,
            env={**os.environ, "DATA_DIR": tmp, "PYTHONIOENCODING": "utf-8"},
        )
    sys.exit(result.returncode)
