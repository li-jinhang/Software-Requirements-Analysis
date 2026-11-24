"""Private server-side model profiles and application bindings (single-worker deployment)."""

from contextlib import contextmanager
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
import json
import sqlite3

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import settings

Use = Literal["default", "extract", "feynman", "encyclopedia", "report"]
TEACHING_USES = ("extract", "feynman", "encyclopedia", "report")


class ModelError(ValueError):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class ModelRequired(ModelError):
    def __init__(self, purpose: Use):
        super().__init__("当前操作需要可用的模型，请先配置并分配模型。", 409)
        self.purpose = purpose


class ProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["api", "chatgpt"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.mode == "api":
            u = urlsplit(self.base_url)
            if (
                not u.hostname
                or u.scheme not in {"http", "https"}
                or u.username
                or u.password
                or u.query
                or u.fragment
            ):
                raise ValueError("Base URL 必须是不含凭据、查询或片段的 HTTP(S) 地址")
            if u.path.rstrip("/").endswith("/chat/completions"):
                raise ValueError("请填写服务根地址，不包含 /chat/completions")
        return self


class ProfileInput(ProfileConfig):
    api_key: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def validate_key(self):
        if self.mode == "api" and self.api_key and any(ch in self.api_key for ch in "\r\n"):
            raise ValueError("API Key 不应包含换行")
        return self


class PortableProfile(ProfileConfig):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ModelArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    profiles: list[PortableProfile]
    bindings: dict[Use, str]

    @model_validator(mode="after")
    def validate_bindings(self):
        ids = {profile.id for profile in self.profiles}
        if len(ids) != len(self.profiles):
            raise ValueError("模型配置 ID 重复")
        if not ids.issuperset(self.bindings.values()):
            raise ValueError("应用绑定指向文件中不存在的模型配置")
        return self


@contextmanager
def store():
    directory = settings.data_dir / "model-settings"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "profiles.sqlite3"
    path.touch(mode=0o600, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,
                mode TEXT NOT NULL,model TEXT NOT NULL,base_url TEXT NOT NULL,api_key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bindings(purpose TEXT PRIMARY KEY,profile TEXT NOT NULL REFERENCES profiles(id));
            CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1),credentials TEXT NOT NULL);
        """
        )
        with db:
            yield db
    finally:
        db.close()


def dashboard():
    with store() as db:
        return {
            "profiles": [
                dict(r)
                for r in db.execute(
                    "SELECT id,name,mode,model,base_url,api_key!='' AS has_key FROM profiles ORDER BY rowid"
                )
            ],
            "bindings": dict(db.execute("SELECT purpose,profile FROM bindings")),
            "environment_live": not settings.mock_llm
            and bool(settings.openai_api_key.get_secret_value()),
        }


def export_archive():
    with store() as db:
        db.execute("BEGIN")
        return {
            "version": 1,
            "profiles": [
                dict(row)
                for row in db.execute("SELECT id,name,mode,model,base_url FROM profiles ORDER BY rowid")
            ],
            "bindings": dict(db.execute("SELECT purpose,profile FROM bindings")),
        }


def import_archive(archive: ModelArchive):
    with store() as db:
        db.execute("BEGIN IMMEDIATE")
        added = 0
        for profile in archive.profiles:
            url = profile.base_url.rstrip("/") if profile.mode == "api" else ""
            added += db.execute(
                """INSERT INTO profiles(id,name,mode,model,base_url,api_key) VALUES(?,?,?,?,?,'')
                ON CONFLICT(id) DO NOTHING""",
                (profile.id, profile.name, profile.mode, profile.model, url),
            ).rowcount
        bound = 0
        for purpose, profile_id in archive.bindings.items():
            bound += db.execute(
                "INSERT INTO bindings VALUES(?,?) ON CONFLICT(purpose) DO NOTHING",
                (purpose, profile_id),
            ).rowcount
        return {"added": added, "skipped": len(archive.profiles) - added, "bindings_added": bound}


def profile(id):
    with store() as db:
        row = db.execute("SELECT * FROM profiles WHERE id=?", (id,)).fetchone()
        if row is None:
            raise ModelError("模型方案不存在", 404)
        return dict(row)


def save_profile(body, id=None):
    with store() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM profiles WHERE id=?", (id,)).fetchone()
        if id and not old:
            raise ModelError("模型方案不存在", 404)
        key = body.api_key or ""
        url = body.base_url.rstrip("/") if body.mode == "api" else ""
        if body.mode == "api" and body.api_key is None and old:
            if old["base_url"] != url and old["api_key"]:
                raise ModelError("更换 API 地址时请重新填写密钥，或明确选择清除密钥", 400)
            key = old["api_key"]
        first = old is None and not db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone()
        id = id or uuid4().hex
        db.execute(
            """INSERT INTO profiles VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,mode=excluded.mode,model=excluded.model,base_url=excluded.base_url,api_key=excluded.api_key""",
            (id, body.name, body.mode, body.model, url, key if body.mode == "api" else ""),
        )
        if first:
            db.execute("INSERT INTO bindings VALUES('default',?)", (id,))
        return id


def bind(purpose, id):
    with store() as db:
        if id is None:
            db.execute("DELETE FROM bindings WHERE purpose=?", (purpose,))
        else:
            try:
                db.execute("INSERT OR REPLACE INTO bindings VALUES(?,?)", (purpose, id))
            except sqlite3.IntegrityError:
                raise ModelError("模型方案不存在", 404) from None


def delete_profile(id):
    with store() as db:
        try:
            db.execute("DELETE FROM profiles WHERE id=?", (id,))
        except sqlite3.IntegrityError:
            raise ModelError("请先解除该方案的默认及应用绑定", 409) from None


def resolve(purpose):
    with store() as db:
        row = db.execute(
            """SELECT p.* FROM profiles p JOIN bindings b ON p.id=b.profile
            WHERE b.purpose IN (?, 'default') ORDER BY (b.purpose=?) DESC LIMIT 1""",
            (purpose, purpose),
        ).fetchone()
        if row:
            if row["mode"] == "chatgpt" and not db.execute("SELECT 1 FROM account WHERE id=1").fetchone():
                return None
            return dict(row)
    if not settings.mock_llm and settings.openai_api_key.get_secret_value():
        return dict(
            mode="api",
            base_url=settings.openai_base_url.rstrip("/"),
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
        )
    return None


def require_profile(purpose: Use):
    profile = resolve(purpose)
    if profile is None:
        raise ModelRequired(purpose)
    return profile


def account_data():
    with store() as db:
        row = db.execute("SELECT credentials FROM account WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None


def save_account(value):
    with store() as db:
        if value is None:
            db.execute("DELETE FROM account")
        else:
            db.execute("INSERT OR REPLACE INTO account VALUES(1,?)", (json.dumps(value),))
