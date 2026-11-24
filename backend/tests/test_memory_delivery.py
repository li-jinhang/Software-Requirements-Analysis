import json
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DB


@pytest.fixture
def course_db(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from app.core.config import settings
    from app.db.database import Base
    from app.db.models import User, Document, Session, KnowledgeNode, KnowledgeEdge, WeaknessLog

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite:///" + (tmp_path / "feynman.db").as_posix())
    Base.metadata.create_all(engine)
    try:
        with DB(engine) as db:
            db.connection().exec_driver_sql("PRAGMA journal_mode=WAL")
            db.add(
                User(id=1, username="learner", nickname="private", password_hash="hash-sentinel")
            )
            for i in [1, 2]:
                db.add(
                    Document(
                        id=i,
                        user_id=1,
                        title=f"教材{i}",
                        file_type="md",
                        subject="测试",
                        file_path="/private/doc.md",
                        parse_status="completed",
                    )
                )
            for i, doc in [(1, 1), (2, 1), (3, 2)]:
                db.add(Session(id=i, user_id=1, doc_id=doc, title="复讲", persona_type="peer"))
            for i, (name, importance, score) in enumerate(
                [
                    ("基础", 4, 65),
                    ("进阶", 5, 10),
                    ("应用", 5, 5),
                    ("薄弱项", 1, 30),
                    ("熟练项", 5, 90),
                    ("别的资料", 5, 10),
                ],
                1,
            ):
                db.add(
                    KnowledgeNode(
                        id=i,
                        doc_id=2 if i == 6 else 1,
                        concept_name=name,
                        summary="定义",
                        importance=importance,
                        mastery_score=score,
                        status="mastered" if score >= 80 else "learning",
                    )
                )
            for src, dst, kind in [
                (1, 2, "prerequisite"),
                (2, 3, "prerequisite"),
                (3, 1, "contrast"),
            ]:
                db.add(
                    KnowledgeEdge(
                        doc_id=1,
                        source_node_id=src,
                        target_node_id=dst,
                        relation_type=kind,
                        description="关系",
                    )
                )
            db.add(WeaknessLog(session_id=2, node_id=4, detail="薄弱", weakness_type="omission"))
            db.commit()
            yield db
    finally:
        engine.dispose()


def test_learning_order_cycles_and_archive(course_db):
    from app.services import memory_service as m
    from app.db.models import KnowledgeEdge

    db = course_db
    path = m.compute_learning_path(db, 1)
    assert [s["node_id"] for s in path] == [4, 1, 2, 3, 5]
    assert path[2]["prerequisites"] == ["基础"] and "薄弱" in path[0]["reason"]
    assert m.get_memory_context(db, 1, 1)["recommended_learning_path"] == path
    assert m.export_memory_json(db, 1)["推荐学习与复讲路径"] == path
    assert "## 推荐学习与复讲路径" in m.export_memory_markdown(db, 1)
    assert not m.compute_learning_path(db, 999)
    db.add(
        KnowledgeEdge(
            doc_id=1,
            source_node_id=2,
            target_node_id=1,
            relation_type="prerequisite",
            description="成环",
        )
    )
    db.flush()
    path = m.compute_learning_path(db, 1)
    ids = [s["node_id"] for s in path]
    assert sorted(ids) == [1, 2, 3, 4, 5] and ids.index(2) < ids.index(3)
    assert any("成环" in s["reason"] for s in path) and m.compute_learning_path(db, 1) == path
    assert db.query(KnowledgeEdge).count() == 4


def test_recent_lookup_pairs_and_session_scope(course_db):
    from app.db.models import Message, WeaknessLog
    from app.services.memory_service import get_memory_context

    db = course_db
    for i in range(7):
        question = "同一个问题" if i in (5, 6) else f"问题{i}"
        db.add(
            Message(session_id=1, window_type="encyclopedia", sender_role="user", content=question)
        )
        db.add(WeaknessLog(session_id=1, node_id=1, weakness_type="need_lookup", detail=question))
        db.add(
            Message(
                session_id=1,
                window_type="encyclopedia",
                sender_role="assistant",
                content=f"回答{i}" + "原文" * 100,
            )
        )
        db.flush()
    db.add(WeaknessLog(session_id=1, node_id=2, weakness_type="need_lookup", detail="仅有日志"))
    db.add(
        Message(session_id=2, window_type="encyclopedia", sender_role="user", content="其他会话")
    )
    db.add(Message(session_id=1, window_type="feynman", sender_role="user", content="左窗"))
    db.flush()
    rows = get_memory_context(db, 1, 1)["recent_encyclopedia_lookups"]
    assert [r["question"] for r in rows] == [
        "仅有日志",
        "同一个问题",
        "同一个问题",
        "问题4",
        "问题3",
    ]
    assert rows[1]["summary"].startswith("回答6") and rows[2]["summary"].startswith("回答5")
    assert rows[1]["related_concepts"] == ["基础"] and len(rows[1]["summary"]) == 180
    assert "recent_encyclopedia_lookups" not in get_memory_context(db, 1)
    with pytest.raises(ValueError):
        get_memory_context(db, 2, 1)


def test_secret_exports_and_delivery_package(course_db, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import admin
    from app.db.models import CognitiveItem, KnowledgeNode, Message
    from app.services import model_profiles
    from scripts import export_submission_data as package

    db = course_db
    key, access, refresh = (
        "fixture-provider-private",
        "opaque-access-private",
        "opaque-refresh-private",
    )
    profile = model_profiles.save_profile(
        model_profiles.ProfileInput(
            name="私有方案",
            mode="api",
            model="test",
            base_url="https://example.invalid/v1",
            api_key=key,
        )
    )
    model_profiles.save_account({"access": access, "refresh": refresh})
    db.get(KnowledgeNode, 1).user_note = f"不应导出 {key} 或 {access}"
    db.add(CognitiveItem(doc_id=1, node_id=1, section_title="基础", item_order=1,
                         statement="认识前提", key_criteria="解释前提", user_evidence=key,
                         status="partial", issue_note=refresh))
    db.add(
        Message(
            session_id=1,
            window_type="encyclopedia",
            sender_role="user",
            content=refresh,
            meta_json=json.dumps({"nested": {"access_token": "unknown-meta-private"}}),
        )
    )
    # Include a private extension table to guard against copying a database wholesale.
    db.connection().exec_driver_sql("CREATE TABLE private_provider (api_key TEXT)")
    db.connection().exec_driver_sql("INSERT INTO private_provider VALUES ('private-extra-table')")
    db.commit()
    source = tmp_path / "feynman.db"
    monkeypatch.setattr(admin, "db_path", source)
    app = FastAPI()
    app.include_router(admin.router, prefix="/api/v1")
    app.dependency_overrides[admin.admin] = lambda: None
    copies = []
    original = admin.data_export.write_sanitized_db

    def track(src, dst):
        copies.append(dst)
        return original(src, dst)

    monkeypatch.setattr(admin.data_export, "write_sanitized_db", track)
    forbidden = (
        key,
        access,
        refresh,
        "hash-sentinel",
        "unknown-meta-private",
        "private-extra-table",
    )
    with TestClient(app) as client:
        for fmt in ("json", "db"):
            response = client.get("/api/v1/admin/export/" + fmt)
            assert response.status_code == 200
            assert not any(s.encode() in response.content for s in forbidden)
            assert not copies[-1].parent.exists()
            if fmt == "json":
                assert "password_hash" not in response.json()["users"][0]
                assert response.json()["cognitive_items"][0]["user_evidence"] == "[REDACTED]"
                assert response.json()["messages"][0]["meta_json"] == {"nested": {}}
            else:
                exported = tmp_path / "download.db"
                exported.write_bytes(response.content)
                with closing(sqlite3.connect(exported)) as clean:
                    assert clean.execute(
                        "SELECT password_hash,file_path FROM users,documents LIMIT 1"
                    ).fetchone() == ("", "")
                    assert clean.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert model_profiles.profile(profile)["api_key"] == key
    assert model_profiles.account_data() == {"access": access, "refresh": refresh}
    assert db.get(KnowledgeNode, 1).user_note.endswith(access)
    assert (
        db.connection().exec_driver_sql("SELECT password_hash FROM users").scalar()
        == "hash-sentinel"
    )
    out = package.build(source, tmp_path)
    assert out == package.build(source, tmp_path)
    assert {p.name for p in out.iterdir()} == {
        "feynman_sanitized.db",
        "backend_data_snapshot.json",
        "openapi.json",
        "DATA_README.md",
        "memory_sample.md",
        "sample_docs",
    }
    assert len(list((out / "sample_docs").glob("*.md"))) >= 2
    assert "推荐学习与复讲路径" in (out / "memory_sample.md").read_text(encoding="utf8")
    assert (
        "/api/v1/documents/{doc_id}/learning-path"
        in json.loads((out / "openapi.json").read_bytes())["paths"]
    )
    assert all(
        not any(s.encode() in p.read_bytes() for s in forbidden)
        for p in out.rglob("*")
        if p.is_file()
    )
    # Export before the first app restart on a teammate's pre-target/pre-rubric database.
    with closing(sqlite3.connect(source)) as old:
        old.execute("DROP TABLE cognitive_items")
        old.execute("ALTER TABLE sessions RENAME COLUMN target_node_id TO previous_unused_target")
        old.commit()
    legacy = package.build(source, tmp_path)
    contents = json.loads((legacy / "backend_data_snapshot.json").read_bytes())
    assert contents["sessions"][0]["target_node_id"] is None and contents["cognitive_items"]
    (out / "sample_docs" / "unsafe.md").write_text("API_KEY=" + key)
    with pytest.raises(ValueError):
        package.inspect_files(out, [key])


def test_long_document_concurrency_is_bounded(course_db, monkeypatch):
    from app.services import llm_client as llm

    lock = threading.Lock()
    active = peak = 0

    def fake(_, schema, prompt, material):
        nonlocal active, peak
        if schema is llm.Digest:
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return llm.Digest(notes=material[:10])
        return llm.local_extract(material, "测试")

    monkeypatch.setattr(llm, "structured", fake)
    llm.live_extract(None, "学" * (llm.SEGMENT_SIZE * 7), "课程")
    assert 2 <= peak <= 3
