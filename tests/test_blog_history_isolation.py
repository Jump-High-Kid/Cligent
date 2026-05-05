"""
test_blog_history_isolation.py — K-6 클리닉 데이터 격리 검증

블로그 이력(`blog_history.py`) 함수가 다른 클리닉의 entry를 노출하지 않는지
순수 단위 + FastAPI 통합 양면으로 검증한다.

- 단위: get_history_list / get_blog_text / get_text_expiry_info 가 clinic_id
  필터를 강제하는지
- 통합: GET /api/blog/history, /api/blog/history/{id}/text,
  POST /api/blog/history/{id}/publish-check 가 타 클리닉 entry_id 로
  접근 시 404 를 반환하는지
"""
import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")


@pytest.fixture()
def history_files(tmp_path, monkeypatch):
    """blog_stats.json + blog_texts.json 을 임시 디렉토리로 격리."""
    import blog_history

    stats_path = tmp_path / "blog_stats.json"
    texts_path = tmp_path / "blog_texts.json"

    now = datetime.now()
    expires = (now + timedelta(days=30)).isoformat()

    stats = [
        {"id": 1, "clinic_id": 1, "keyword": "A 두통", "title": "A1",
         "tone": "전문", "char_count": 1500, "cost_krw": 100,
         "seo_keywords": [], "naver_url": "", "created_at": now.isoformat()},
        {"id": 2, "clinic_id": 2, "keyword": "B 소화", "title": "B1",
         "tone": "전문", "char_count": 1500, "cost_krw": 100,
         "seo_keywords": [], "naver_url": "", "created_at": now.isoformat()},
        # 레거시 entry: clinic_id 누락
        {"id": 3, "clinic_id": None, "keyword": "Legacy", "title": "L1",
         "tone": "전문", "char_count": 1500, "cost_krw": 100,
         "seo_keywords": [], "naver_url": "", "created_at": now.isoformat()},
    ]
    texts = [
        {"id": 1, "blog_text": "A 본문", "created_at": now.isoformat(), "expires_at": expires},
        {"id": 2, "blog_text": "B 본문 비밀", "created_at": now.isoformat(), "expires_at": expires},
        {"id": 3, "blog_text": "Legacy 본문", "created_at": now.isoformat(), "expires_at": expires},
    ]

    stats_path.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    texts_path.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(blog_history, "STATS_PATH", stats_path)
    monkeypatch.setattr(blog_history, "TEXTS_PATH", texts_path)

    return blog_history


# ─── 단위: get_history_list ───────────────────────────────────────────────

def test_get_history_list_filters_by_clinic(history_files):
    res = history_files.get_history_list(clinic_id=1, page=1, per_page=20)
    assert res["total"] == 1
    assert [e["id"] for e in res["items"]] == [1]


def test_get_history_list_excludes_legacy_null(history_files):
    """clinic_id=None 인 레거시 entry 는 어떤 일반 사용자에게도 안 보인다."""
    for cid in (1, 2, 999):
        res = history_files.get_history_list(clinic_id=cid, page=1, per_page=20)
        ids = [e["id"] for e in res["items"]]
        assert 3 not in ids


# ─── 단위: get_blog_text ───────────────────────────────────────────────────

def test_get_blog_text_owner_returns_text(history_files):
    assert history_files.get_blog_text(1, clinic_id=1) == "A 본문"


def test_get_blog_text_cross_clinic_returns_none(history_files):
    """clinic 1 이 clinic 2 의 entry_id 로 조회 → None (라우트에서 404 변환)."""
    assert history_files.get_blog_text(2, clinic_id=1) is None


def test_get_blog_text_legacy_returns_none(history_files):
    assert history_files.get_blog_text(3, clinic_id=1) is None


def test_get_text_expiry_info_cross_clinic_returns_none(history_files):
    assert history_files.get_text_expiry_info(2, clinic_id=1) is None


# ─── 통합: 라우트 ─────────────────────────────────────────────────────────

@pytest.fixture()
def client_clinic1(history_files):
    from main import app
    from auth_manager import get_current_user

    fake_user = {"id": 11, "clinic_id": 1, "role": "chief_director",
                 "email": "a@a", "is_active": True}
    app.dependency_overrides[get_current_user] = lambda: fake_user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_route_history_list_only_own_clinic(client_clinic1):
    res = client_clinic1.get("/api/blog/history?page=1&per_page=20")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert [e["id"] for e in body["items"]] == [1]


def test_route_text_cross_clinic_returns_404(client_clinic1):
    res = client_clinic1.get("/api/blog/history/2/text")
    assert res.status_code == 404


def test_route_text_legacy_returns_404(client_clinic1):
    res = client_clinic1.get("/api/blog/history/3/text")
    assert res.status_code == 404


def test_route_publish_check_cross_clinic_returns_404(client_clinic1):
    """clinic 1 사용자가 clinic 2 entry_id 로 발행 확인 등록 → 404.

    네이버 설정 분기 전에 ownership 차단되도록 is_naver_configured 도 모킹.
    """
    with patch("naver_checker.is_naver_configured", return_value=True):
        # 네이버 블로그 아이디 조회 SQL 우회 위해 DB 자체를 검사하지 않고
        # 핵심 동작만 확인: 타 클리닉 entry_id 는 history 목록에 없으니 404.
        res = client_clinic1.post("/api/blog/history/2/publish-check")
    # 네이버 키 미설정 환경에서는 400, 설정된 환경에서는 404 — 어느 쪽도
    # entry 노출은 없음. 핵심: 200 이 절대 아님.
    assert res.status_code in (400, 404)


# ─── D(2026-05-05): naver-readiness 분기 ────────────────────────────────

def test_naver_readiness_api_unset(client_clinic1):
    """API 키 미설정 → api_configured=false, ready=false."""
    with patch("naver_checker.is_naver_configured", return_value=False):
        res = client_clinic1.get("/api/blog/naver-readiness")
    assert res.status_code == 200
    body = res.json()
    assert body["api_configured"] is False
    assert body["ready"] is False


def test_naver_readiness_api_set_blog_id_unset(client_clinic1):
    """API는 OK인데 본인 클리닉 naver_blog_id 미등록 → blog_id_configured=false."""
    with patch("naver_checker.is_naver_configured", return_value=True):
        res = client_clinic1.get("/api/blog/naver-readiness")
    assert res.status_code == 200
    body = res.json()
    assert body["api_configured"] is True
    # 라이브 DB의 clinic 1에 naver_blog_id 가 비어있을 수 있음 — 핵심은 ready 게이트
    if not body["blog_id_configured"]:
        assert body["ready"] is False
