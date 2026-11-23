"""Run the local FastAPI + Vite development pair from the repository checkout."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def main():
    backend = ROOT / "backend"
    frontend = ROOT / "frontend"
    python = backend / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    vite = frontend / "node_modules/vite/bin/vite.js"
    node = shutil.which("node")
    if not (python.is_file() and node and vite.is_file()):
        sys.exit("请先按 README 安装 backend/.venv 与 frontend 的 npm 依赖。")
    children = []
    try:
        children.append(
            subprocess.Popen(
                [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                cwd=backend,
            )
        )
        children.append(
            subprocess.Popen(
                [node, vite, "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
                cwd=frontend,
                env={**os.environ, "VITE_BACKEND_URL": "http://127.0.0.1:8000"},
            )
        )
        print(
            "工作台 http://127.0.0.1:5173   API http://127.0.0.1:8000/docs   Ctrl+C 结束",
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
