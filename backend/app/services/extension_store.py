"""User-owned capability packages and connections; separate from business exports."""
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import sqlite3
import stat
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import yaml
from fastapi import HTTPException
from app.core.config import settings


@contextmanager
def store():
    folder = settings.data_dir / "capabilities"
    folder.mkdir(exist_ok=True)
    with sqlite3.connect(folder / "private.sqlite3", timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.executescript("""
        CREATE TABLE IF NOT EXISTS skills (
          id TEXT PRIMARY KEY, owner INTEGER NOT NULL, name TEXT NOT NULL,
          description TEXT NOT NULL, version TEXT NOT NULL, body TEXT NOT NULL,
          files TEXT NOT NULL, metadata TEXT NOT NULL, reason TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1, shared INTEGER NOT NULL DEFAULT 0,
          UNIQUE(owner,name));
        CREATE TABLE IF NOT EXISTS servers (
          id TEXT PRIMARY KEY, owner INTEGER NOT NULL, name TEXT NOT NULL,
          url TEXT NOT NULL, token TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          tools TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT '未连接');
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session_id INTEGER NOT NULL,
          kind TEXT NOT NULL, capability_id TEXT NOT NULL, input TEXT NOT NULL,
          status TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
          metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        yield db


def purge_account(owner):
    """Remove private extensions when the account's business deletion commits."""
    with store() as db:
        for table in ("skills", "servers", "runs"):
            db.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))


def purge_sessions(owner, session_ids):
    """Remove capability run logs associated with deleted sessions."""
    ids = [int(i) for i in session_ids]
    if not ids:
        return
    with store() as db:
        db.execute(
            f"DELETE FROM runs WHERE owner=? AND session_id IN ({','.join('?' * len(ids))})",
            (owner, *ids),
        )


def skill_out(row, owner):
    return {k: row[k] for k in ("id", "name", "description", "version", "reason")} | {
        "enabled": bool(row["enabled"]), "shared": bool(row["shared"]),
        "editable": row["owner"] == owner,
        "available": bool(row["enabled"] and not row["reason"]),
        "files": list(json.loads(row["files"])),
    }


def skills(owner):
    with store() as db:
        return [skill_out(r, owner) for r in db.execute(
            "SELECT * FROM skills WHERE owner=? OR shared=1 ORDER BY name,id", (owner,))]


def skill(id, owner):
    with store() as db:
        row = db.execute("SELECT * FROM skills WHERE id=? AND (owner=? OR shared=1)", (id, owner)).fetchone()
        if not row:
            raise HTTPException(404, "技能不存在")
        return dict(row)


def import_skill(data, owner, replace=False):
    if len(data) > 10 * 1024**2:
        raise HTTPException(413, "技能包不能超过 10 MB")
    try:
        with ZipFile(BytesIO(data)) as archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            if len(members) > 300 or sum(i.file_size for i in members) > 20 * 1024**2:
                raise ValueError("解压后超过 20 MB 或 300 个文件")
            for i in members:
                p = PurePosixPath(i.filename.replace("\\", "/"))
                if p.is_absolute() or ".." in p.parts or ":" in str(p) or stat.S_ISLNK(i.external_attr >> 16):
                    raise ValueError("技能包包含非法路径或符号链接")
            entries = [i for i in members if PurePosixPath(i.filename).name == "SKILL.md"]
            if len(entries) != 1:
                raise ValueError("每个 ZIP 必须包含且仅包含一份 SKILL.md")
            entry = entries[0]
            if entry.file_size > 128 * 1024:
                raise ValueError("SKILL.md 超过 128 KB")
            raw = archive.read(entry).decode("utf-8-sig")
            match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)\Z", raw, re.S)
            if not match:
                raise ValueError("SKILL.md 需要 YAML 元数据和正文")
            meta = yaml.safe_load(match[1])
            if not isinstance(meta, dict):
                raise ValueError("技能元数据必须是对象")
            name, description = meta.get("name"), meta.get("description")
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
                raise ValueError("name 需为 1–64 个小写字母、数字或短横线")
            if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024 or not match[2].strip():
                raise ValueError("description 和技能正文不能为空，描述最多 1024 字")
            prefix = str(PurePosixPath(entry.filename).parent)
            prefix = "" if prefix == "." else prefix + "/"
            files = {}
            scripted = False
            for i in members:
                if i.filename == entry.filename or not i.filename.startswith(prefix):
                    continue
                rel = i.filename[len(prefix):]
                scripted |= rel.startswith("scripts/") or PurePosixPath(rel).suffix.lower() in {".py", ".sh", ".exe", ".bat", ".js", ".ps1"}
                if i.file_size <= 128 * 1024 and PurePosixPath(rel).suffix.lower() in {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}:
                    try:
                        files[rel] = archive.read(i).decode("utf-8-sig")
                    except UnicodeDecodeError:
                        files[rel] = "[非文本资源]"
                else:
                    files[rel] = "[资源未加载为文本]"
            if len(json.dumps(files)) > 1024 * 1024:
                raise ValueError("技能文本资源总量超过 1 MB")
            reason = "包含可执行脚本，需要适配执行环境" if scripted else ""
            if meta.get("allowed-tools") or meta.get("compatibility"):
                reason = reason or "声明了工具或环境依赖，需要适配后才能执行"
            version = str((meta.get("metadata") or {}).get("version", meta.get("version", "1.0")))[:64]
            metadata = json.dumps(meta, ensure_ascii=False)
    except (BadZipFile, ValueError, UnicodeError, yaml.YAMLError, RuntimeError, AttributeError, RecursionError) as exc:
        raise HTTPException(422, "技能包无效：" + str(exc)[:250]) from None
    with store() as db:
        old = db.execute("SELECT id FROM skills WHERE owner=? AND name=?", (owner, name)).fetchone()
        if old and not replace:
            raise HTTPException(409, "同名技能已安装；勾选“更新同名技能”后重新导入")
        id = old["id"] if old else uuid4().hex
        db.execute("""INSERT INTO skills(id,owner,name,description,version,body,files,metadata,reason)
          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
          description=excluded.description,version=excluded.version,body=excluded.body,
          files=excluded.files,metadata=excluded.metadata,reason=excluded.reason""",
          (id, owner, name, description, version, match[2], json.dumps(files, ensure_ascii=False), metadata, reason))
    return skill_out(skill(id, owner), owner)


def server(id, owner):
    with store() as db:
        row = db.execute("SELECT * FROM servers WHERE id=? AND owner=?", (id, owner)).fetchone()
        if not row:
            raise HTTPException(404, "MCP 连接不存在")
        return dict(row)


def servers(owner):
    with store() as db:
        return [server_out(dict(r)) for r in db.execute("SELECT * FROM servers WHERE owner=? ORDER BY name", (owner,))]


def server_out(row):
    return {k: row[k] for k in ("id", "name", "url", "status")} | {
        "enabled": bool(row["enabled"]), "has_token": bool(row["token"]), "tools": json.loads(row["tools"]),
    }


def update_status(id, tools=None, status="已连接"):
    with store() as db:
        if tools is None:
            db.execute("UPDATE servers SET status=? WHERE id=?", (status, id))
        else:
            db.execute("UPDATE servers SET status=?,tools=? WHERE id=?", (status, json.dumps(tools, ensure_ascii=False), id))
