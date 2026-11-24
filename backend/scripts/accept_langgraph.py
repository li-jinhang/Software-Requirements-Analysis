"""Interactive local U0003/U0004 acceptance client; no frontend/npm required."""

import argparse
import json
import sys
from time import perf_counter

import httpx


def main():
    parser = argparse.ArgumentParser(description="LangGraph 本地验收客户端")
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--username", default="student")
    parser.add_argument("--password", default="123456")
    parser.add_argument("--persona", choices=["child", "peer", "examiner"], default="peer")
    parser.add_argument("--doc", type=int, help="资料 ID，默认选择服务端第一份资料")
    parser.add_argument("--node", type=int, help="目标知识点 ID")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url.rstrip("/") + "/api/v1/", trust_env=False, timeout=120) as api:
        response = api.post("auth/login", json={"username": args.username, "password": args.password})
        response.raise_for_status()
        api.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        docs = api.get("documents").raise_for_status().json()
        if not docs:
            sys.exit("账号没有资料。请在 /docs 上传课程后重试。")
        doc = args.doc or docs[0]["id"]
        nodes = api.get(f"documents/{doc}/graph").raise_for_status().json()["nodes"]
        print("知识点：", "；".join(f"{n['id']} {n['concept_name']}" for n in nodes))
        body = {"doc_id": doc, "persona_type": args.persona}
        if args.node:
            body["node_id"] = args.node
        session = api.post("sessions", json=body).raise_for_status().json()
        sid = session["id"]
        print(f"\n验收会话 {sid}。/role child|peer|examiner 切角色；/lookup 问题 查证；/history 查看记录；/exit 退出。")
        print("本地默认是明确标记的规则演示；真实评估需配置 backend/.env 并重启后端。\n")

        def stream(path, content=None):
            start = perf_counter()
            first_text = None
            completed = False
            opts = {"json": {"content": content}} if content is not None else {}
            with api.stream("POST", path, **opts) as response:
                response.raise_for_status()
                kind = ""
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:])
                        if kind == "token":
                            if first_text is None:
                                first_text = perf_counter() - start
                            print(data["delta"], end="", flush=True)
                        elif kind == "memory_update":
                            print("\n掌握度：", json.dumps(data["updates"], ensure_ascii=False))
                        elif kind == "citation":
                            print("原文引用：", "；".join(f"{c['doc_title']} #{c['chunk_index']}" for c in data["citations"]))
                        elif kind == "error":
                            print("\n本轮失败：", data["detail"])
                        elif kind == "done":
                            completed = True
                print(f"\n首字 {first_text:.3f}s" if first_text is not None else "\n未输出文本")
                print("已保存。\n" if completed else "本轮未确认保存，请先 /history 检查再重试。\n")

        stream(f"sessions/{sid}/opening/stream")
        while True:
            text = input("我：").strip()
            if not text:
                continue
            if text == "/exit":
                return
            if text == "/history":
                rows = api.get(f"sessions/{sid}/messages").raise_for_status().json()
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif text.startswith("/role "):
                response = api.patch(f"sessions/{sid}/persona", json={"persona_type": text[6:].strip()})
                print(response.json())
            else:
                side = "encyclopedia" if text.startswith("/lookup ") else "feynman"
                content = text[8:] if side == "encyclopedia" else text
                stream(f"sessions/{sid}/{side}/stream", content)


if __name__ == "__main__":
    # Keep Chinese output readable in redirected Codex/PowerShell terminals.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (httpx.HTTPError, EOFError, KeyboardInterrupt) as exc:
        print("\n验收客户端结束：", type(exc).__name__)
