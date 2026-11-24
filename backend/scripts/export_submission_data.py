"""Generate the course data hand-in from one sanitized SQLite snapshot."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

BACKEND = Path(__file__).resolve().parents[1]
OUTPUT = BACKEND.parent / "dist_submission_data"
VENV = BACKEND / ".venv"
python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if __name__ == "__main__" and python.exists() and Path(sys.prefix).resolve() != VENV:
    sys.exit(subprocess.call([str(python), __file__]))

sys.path.insert(0, str(BACKEND))
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from app.db.database import db_path
from app.db.models import Document
from app.main import app
from app.services import data_export
from app.services.memory_service import export_memory_markdown


def write_json(path, value):
    path.write_text(
        json.dumps(jsonable_encoder(value), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def inspect_files(folder, secrets):
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="ignore")
        if (
            any(s.encode() in raw or s in path.name for s in secrets)
            or data_export.TOKEN.search(text)
            or data_export.BEARER.search(text)
        ):
            raise ValueError("交付文件中检测到疑似凭据，已取消发布")
        if path.suffix == ".md" and any(
            not m[2].startswith("[REDACTED") for m in data_export.ASSIGNMENT.finditer(text)
        ):
            raise ValueError("Markdown 含疑似明文凭据，已取消发布")


def build(source, parent=OUTPUT.parent):
    parent = parent.resolve()
    target = parent / OUTPUT.name
    if target.resolve() != target or target.is_symlink():
        raise ValueError("交付目录不能指向其他位置")
    with tempfile.TemporaryDirectory(dir=parent) as work:
        stage = Path(work) / "new"
        stage.mkdir()
        snapshot = stage / "feynman_sanitized.db"
        secrets = data_export.write_sanitized_db(source, snapshot)
        with data_export.snapshot_session(snapshot) as db:
            snapshot_rows = data_export.snapshot_json(db)
            if "cognitive_items" not in snapshot_rows:
                raise ValueError("交付快照缺少认知文档表")
            write_json(stage / "backend_data_snapshot.json", snapshot_rows)
            course = db.scalar(
                select(Document)
                .where(Document.parse_status == "completed")
                .order_by(Document.id)
                .limit(1)
            )
            if not course:
                raise ValueError("业务库中没有已解析的课程，无法生成记忆档案样例")
            (stage / "memory_sample.md").write_text(
                "# " + course.title + "\n\n" + export_memory_markdown(db, course.id),
                encoding="utf-8",
            )
        write_json(stage / "openapi.json", app.openapi())
        samples = stage / "sample_docs"
        samples.mkdir()
        for doc in sorted((BACKEND / "data/sample_docs").glob("*.md")):
            shutil.copyfile(doc, samples / doc.name)
        (stage / "DATA_README.md").write_text(
            """# 智溯费曼后台数据交付说明

SQLite 与 JSON 来自同一份业务快照，可用 SQLite 查看器打开。账号已化名，密码哈希及源文件路径已清空。模型方案、OAuth 账号库、上传原件和向量索引均不随包分发。运行演示请使用项目正常初始化的业务库；交付副本用于审阅数据。

| 表 | 内容 |
| --- | --- |
| users | 化名用户与角色 |
| documents | 课程、解析状态与摘要 |
| knowledge_nodes | 概念、重要度、掌握度与个人笔记 |
| knowledge_edges | 知识点之间有方向的关系 |
| cognitive_items | 认知条目、掌握标记与纠偏依据，复习阶段、上次及下次复习时间、评估与跳过次数 |
| sessions | 教学或复习会话，含选定条目顺序、复习进度和完成状态 |
| messages | 左右窗消息及结构化元数据 |
| weakness_logs | 薄弱点、查证与解决状态 |
| diagnostic_reports | 四维诊断、评语与复习计划 |

安装项目依赖后，在仓库根目录运行 `python dev.py`。浏览器打开 http://127.0.0.1:8000/docs 查看 Swagger。预置学生为 `student / 123456`，管理员为 `admin / admin123`。用 POST /api/v1/auth/login 获取会话令牌后，在 Swagger 的 Authorize 中登录。

学生可读取 GET /api/v1/documents/{doc_id}/learning-path、GET /api/v1/documents/{id}/memory，以及 GET /api/v1/documents/{id}/memory/export?format=md。管理员可用 GET /api/v1/admin/export/json 和 GET /api/v1/admin/export/db 下载脱敏快照。

openapi.json 是本版本的接口定义。sample_docs/ 提供上传样例，memory_sample.md 展示已解析课程的结构化记忆和推荐复讲顺序。
""",
            encoding="utf-8",
        )
        inspect_files(stage, secrets)
        # Publish only after every artifact passes; restore the prior package on rename failure.
        previous = Path(work) / "previous"
        if target.exists():
            target.rename(previous)
        try:
            stage.rename(target)
        except OSError:
            if previous.exists():
                previous.rename(target)
            raise
    return target


if __name__ == "__main__":
    if db_path.exists():
        result = build(db_path)
    else:
        # A fresh clone has no ignored database. Seed a disposable demo, never the live data directory.
        with tempfile.TemporaryDirectory() as temp:
            env = {
                **os.environ,
                "DATA_DIR": temp,
                "MOCK_LLM": "true",
                "SEED_DEMO": "true",
                "EMBEDDING_PROVIDER": "local_lite",
            }
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from app.main import app\nfrom fastapi.testclient import TestClient\n"
                    "with TestClient(app): pass",
                ],
                cwd=BACKEND,
                env=env,
                check=True,
            )
            result = build(Path(temp) / "feynman.db")
    print(f"SQLite 完整性与交付文件凭据自检通过：{result}")
