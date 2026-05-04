"""
test_admin_usage_feature_key.py — admin 라우트의 feature 키 정규화 회귀

배경 (2026-05-04 발견):
  routers/admin.py 가 usage_logs.feature='blog_generate' 로 쿼리하지만
  실제 저장은 'blog_generation' (routers/blog.py + plan_guard.py + plan_notify.py)
  로 통일되어 있어 어드민 페이지의 블로그 카운트가 항상 0 으로 표시되던 회귀.

검증 항목:
  1. /api/admin/usage 가 'blog_generation' 행을 반영해 blog_all_time / blog_this_month 양수로 응답
  2. /api/admin/clinics 의 blog_this_month / usage_total 양수로 응답
  3. 잘못된 'blog_generate' 행은 카운트되면 안 됨 (양방향 회귀 차단)
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    import db_manager
    orig = db_manager.DB_PATH
    db_manager.DB_PATH = tmp_path / "kpi_test.db"
    db_manager.init_db()

    now = datetime.now(timezone.utc).isoformat()
    with db_manager.get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots, is_admin_clinic) "
            "VALUES (1, 'Cligent Admin', 100, 1)"
        )
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots, is_admin_clinic) "
            "VALUES (2, '테스트 한의원', 5, 0)"
        )
        # 정상 저장 — feature='blog_generation' 3건
        for _ in range(3):
            conn.execute(
                "INSERT INTO usage_logs (clinic_id, feature, used_at) VALUES (?, ?, ?)",
                (2, "blog_generation", now),
            )
        # 회귀 차단 — 잘못된 키는 카운트되면 안 됨
        conn.execute(
            "INSERT INTO usage_logs (clinic_id, feature, used_at) VALUES (?, ?, ?)",
            (2, "blog_generate", now),
        )
        # prompt_copy 1건
        conn.execute(
            "INSERT INTO usage_logs (clinic_id, feature, used_at) VALUES (?, ?, ?)",
            (2, "prompt_copy", now),
        )

    yield
    db_manager.DB_PATH = orig


@pytest.fixture
def client(monkeypatch):
    # main 임포트 시 load_dotenv(override=True) 로 .env 가 우선되므로
    # 임포트 후 monkeypatch.setenv 로 테스트 값을 강제 적용한다 (dep 가 요청마다 os.getenv 호출).
    os.environ.setdefault("SECRET_KEY",      "test-secret-key-32chars-minimum!!")
    os.environ.setdefault("ADMIN_CLINIC_ID", "1")
    os.environ.setdefault("ADMIN_USER_ID",   "1")
    from main import app
    monkeypatch.setenv("ADMIN_SECRET", "test-admin")
    return TestClient(app, raise_server_exceptions=False)


def _auth():
    return {"Authorization": "Bearer test-admin"}


def test_admin_usage_counts_blog_generation(client):
    """blog_generation 3건 → blog_all_time=3, blog_this_month=3"""
    res = client.get("/api/admin/usage", headers=_auth())
    assert res.status_code == 200
    body = res.json()
    assert body["blog_all_time"] == 3, (
        f"blog_generation 3건이 반영되지 않음 — 'blog_generate' 오타 회귀 의심: {body}"
    )
    assert body["blog_this_month"] == 3
    assert body["prompt_copy_this_month"] == 1


def test_admin_clinics_counts_blog_generation(client):
    """clinic 행에 blog_this_month=3 으로 잡혀야 함"""
    res = client.get("/api/admin/clinics", headers=_auth())
    assert res.status_code == 200
    clinics = res.json()["clinics"]
    target = next((c for c in clinics if c["id"] == 2), None)
    assert target is not None
    assert target["blog_this_month"] == 3, (
        f"clinic.blog_this_month 가 0 — 'blog_generate' 오타 회귀: {target}"
    )


def test_admin_usage_ranking_picks_blog_generation(client):
    """랭킹 LEFT JOIN 의 feature 필터도 같이 정정됐는지"""
    res = client.get("/api/admin/usage", headers=_auth())
    assert res.status_code == 200
    ranking = res.json()["ranking"]
    target = next((r for r in ranking if r["id"] == 2), None)
    assert target is not None
    assert target["blog_this_month"] == 3
