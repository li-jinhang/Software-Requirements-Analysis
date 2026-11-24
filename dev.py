"""Run the local FastAPI + Vite development pair from the repository checkout."""

import os
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def ensure_langgraph(python: Path, backend: Path):
    probe = [
        python,
        "-c",
        "from langgraph.config import get_stream_writer; "
        "from langgraph.graph import END, START, StateGraph; "
        "from langgraph.runtime import Runtime",
    ]
    if subprocess.run(probe, cwd=backend, capture_output=True).returncode == 0:
        return
    print("正在为 backend/.venv 同步后端依赖…", flush=True)
    install = [
        python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
        "-r", str(backend / "requirements.txt"),
    ]
    if subprocess.run(install, cwd=backend).returncode != 0:
        sys.exit("后端依赖安装失败，请查看上方 pip 输出。")
    if subprocess.run(probe, cwd=backend).returncode != 0:
        sys.exit("LangGraph 仍无法导入，请检查上方 Python 错误。")


def main():
    parser = argparse.ArgumentParser(description="启动原版 React 工作台与本地教学后端")
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--frontend-only", action="store_true", help="连接已运行的后端，仅启动原版前端")
    args = parser.parse_args()
    backend = ROOT / "backend"
    frontend = ROOT / "frontend"
    python = backend / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    vite = frontend / "node_modules/vite/bin/vite.js"
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    bundled_node = Path(sys.base_prefix).parent / "node" / "bin" / "node.exe"
    if not node and bundled_node.is_file():
        node = str(bundled_node)
    if not (python.is_file() and node and vite.is_file()):
        sys.exit("请先按 README 安装 backend/.venv 与 frontend 的 npm 依赖。")
    children = []
    try:
        if not args.frontend_only:
            ensure_langgraph(python, backend)
            children.append(
                subprocess.Popen(
                    [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.backend_port)],
                    cwd=backend,
                )
            )
        children.append(
            subprocess.Popen(
                [node, vite, "--host", "127.0.0.1", "--port", str(args.frontend_port), "--strictPort"],
                cwd=frontend,
                env={**os.environ, "VITE_BACKEND_URL": f"http://127.0.0.1:{args.backend_port}"},
            )
        )
        print(
            f"原版工作台 http://127.0.0.1:{args.frontend_port}   API http://127.0.0.1:{args.backend_port}/docs   Ctrl+C 结束",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(0.25)
        return next(child.returncode for child in children if child.returncode is not None)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            child.wait()


if __name__ == "__main__":
    sys.exit(main())
