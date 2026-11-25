"""Check the startup reset against an isolated, persistent workspace."""

import os
from pathlib import Path
import subprocess
import sys


def test_new_build_restores_only_the_builtin_student(tmp_path):
    code = """
from fastapi.testclient import TestClient
from app.core.config import settings
from app.main import app
from app.services import rag_store

auth = '/api/v1/auth'
documents = '/api/v1/documents'
settings.demo_build_id = 'test-build-a'
with TestClient(app) as api:
    def login(username, password):
        response = api.post(auth + '/login', json={'username': username, 'password': password})
        response.raise_for_status()
        return {'Authorization': 'Bearer ' + response.json()['access_token']}

    def profile(headers):
        return api.get(auth + '/me', headers=headers).json()

    def create(title, headers):
        result = api.post(documents + '/course-libraries', headers=headers, json={'title': title})
        assert result.status_code == 201, result.text
        return result.json()['document']['id']

    def nodes(doc_id):
        return api.get(f'{documents}/{doc_id}/graph', headers=demo).json()['nodes']

    demo = login('student', '123456')
    original = profile(demo)
    initial = {doc['title']: doc for doc in api.get(documents, headers=demo).json()}
    initial_id = initial['机器学习基础：梯度下降与过拟合']['id']
    network_id = initial['计算机网络']['id']
    assert initial['计算机网络']['is_library'] and initial['计算机网络']['chunk_count'] == 101
    sample_progress = [(n['concept_name'], n['mastery_score'], n['user_note']) for n in nodes(initial_id)]
    network_progress = [(n['concept_name'], n['mastery_score'], n['user_note']) for n in nodes(network_id)]
    node = nodes(initial_id)[0]
    extra = create('临时演示资料', demo)
    api.patch(f'{documents}/{initial_id}/nodes/{node["id"]}/note', headers=demo,
        json={'user_note': '本次测试临时更改'}).raise_for_status()
    api.post(auth + '/register', json={
        'username': 'regular', 'password': 'regular-pass', 'nickname': '我的账号'
    }).raise_for_status()
    regular = login('regular', 'regular-pass')
    retained = create('普通账号的资料', regular)

with TestClient(app) as api:
    assert profile(demo) == original
    ids = {d['id'] for d in api.get(documents, headers=demo).json()}
    assert ids == {initial_id, network_id, extra}
    assert nodes(initial_id)[0]['user_note'] == '本次测试临时更改'

settings.demo_build_id = 'test-build-b'
with TestClient(app) as api:
    changed = profile(demo)
    assert changed['id'] == original['id'] and changed['demo_build'] != original['demo_build']
    reset = {doc['title']: doc for doc in api.get(documents, headers=demo).json()}
    assert set(reset) == set(initial)
    new = reset['机器学习基础：梯度下降与过拟合']
    network = reset['计算机网络']
    assert new['id'] != initial_id and network['id'] != network_id
    assert [(n['concept_name'], n['mastery_score'], n['user_note']) for n in nodes(new['id'])] == sample_progress
    assert [(n['concept_name'], n['mastery_score'], n['user_note']) for n in nodes(network['id'])] == network_progress
    assert not rag_store.collection().get(where={'doc_id': initial_id}, include=[])['ids']
    assert rag_store.collection().get(where={'doc_id': new['id']}, include=[])['ids']
    assert not rag_store.collection().get(where={'doc_id': network_id}, include=[])['ids']
    assert len(rag_store.collection().get(where={'doc_id': network['id']}, include=[])['ids']) == 101
    [lesson] = api.get('/api/v1/sessions', headers=demo).json()
    report = api.get(f'/api/v1/sessions/{lesson["id"]}/report', headers=demo).json()
    assert report['clarity_score'] == 91
    assert 'demo_build' not in profile(regular)
    assert [doc['id'] for doc in api.get(documents, headers=regular).json()] == [retained]
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATA_DIR": str(tmp_path), "SEED_DEMO": "true",
             "EMBEDDING_PROVIDER": "local_lite", "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", timeout=55,
    )
    assert result.returncode == 0, result.stdout + result.stderr
