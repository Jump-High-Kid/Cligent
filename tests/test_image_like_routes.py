"""
test_image_like_routes.py — Commit 6c POST/GET /api/blog/image/like 라우트 통합

엔드포인트:
  POST /api/blog/image/like      body: {session_id, image_index, liked}
  GET  /api/blog/image/likes     query: ?session_id=...

검증:
  - POST 정상: 200 + liked·module 반환
  - POST 다른 clinic: 403
  - POST 잘못된 session: 404
  - POST image_index 범위 밖: 400
  - POST UUID 형식 위반: 400
  - GET 정상: 5개 dict
  - GET 다른 clinic: 403
  - GET 잘못된 session: 404

라이브 DB 사용 (test_image_routes.py 와 동일 패턴) — image_sessions·gallery_likes
스키마는 db_manager.init_db() 로 보장.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")

from main import app  # noqa: E402
from auth_manager import get_current_user  # noqa: E402

client = TestClient(app)

FAKE_USER = {
    "id": 1,
    "clinic_id": 1,
    "role": "chief_director",
    "email": "test@test.com",
    "is_active": True,
}


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    yield
    app.dependency_overrides.clear()


def _seed_session(clinic_id: int = 1, modules_json: str = '[1,4,8,2,11]') -> str:
    """라이브 DB 에 image_session 시드. session_id 반환."""
    import db_manager
    sid = str(uuid.uuid4())
    with db_manager.get_db() as conn:
        # FK 만족: clinics row 존재 확인
        clinic_exists = conn.execute(
            "SELECT 1 FROM clinics WHERE id = ?", (clinic_id,)
        ).fetchone()
        if not clinic_exists:
            conn.execute(
                "INSERT INTO clinics (id, name, max_slots) VALUES (?, ?, ?)",
                (clinic_id, f"테스트 한의원 {clinic_id}", 5),
            )
        conn.execute(
            "INSERT INTO image_sessions "
            "(session_id, clinic_id, user_id, blog_keyword, plan_id_at_start, modules_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, clinic_id, 100, "테스트", "standard", modules_json),
        )
    return sid


def _cleanup_likes(session_id: str):
    import db_manager
    with db_manager.get_db() as conn:
        conn.execute("DELETE FROM gallery_likes WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM image_sessions WHERE session_id = ?", (session_id,))


# ── POST /api/blog/image/like ────────────────────────────


class TestPostLike:
    def test_like_success(self):
        sid = _seed_session()
        try:
            r = client.post(
                "/api/blog/image/like",
                json={"session_id": sid, "image_index": 0, "liked": True},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["liked"] is True
            assert body["image_index"] == 0
            assert body["module"] == 1
        finally:
            _cleanup_likes(sid)

    def test_unlike_preserves_row(self):
        sid = _seed_session()
        try:
            client.post("/api/blog/image/like",
                        json={"session_id": sid, "image_index": 0, "liked": True})
            r = client.post("/api/blog/image/like",
                            json={"session_id": sid, "image_index": 0, "liked": False})
            assert r.status_code == 200
            assert r.json()["liked"] is False
        finally:
            _cleanup_likes(sid)

    def test_other_clinic_blocked_403(self):
        sid = _seed_session(clinic_id=99)  # 다른 한의원 세션
        try:
            r = client.post(
                "/api/blog/image/like",
                json={"session_id": sid, "image_index": 0, "liked": True},
            )
            assert r.status_code == 403
        finally:
            _cleanup_likes(sid)

    def test_unknown_session_404(self):
        r = client.post(
            "/api/blog/image/like",
            json={"session_id": str(uuid.uuid4()), "image_index": 0, "liked": True},
        )
        assert r.status_code == 404

    @pytest.mark.parametrize("bad_index", [-1, 5, 100])
    def test_image_index_out_of_range_400(self, bad_index):
        sid = _seed_session()
        try:
            r = client.post(
                "/api/blog/image/like",
                json={"session_id": sid, "image_index": bad_index, "liked": True},
            )
            assert r.status_code == 400
        finally:
            _cleanup_likes(sid)

    def test_invalid_uuid_400(self):
        r = client.post(
            "/api/blog/image/like",
            json={"session_id": "not-a-uuid", "image_index": 0, "liked": True},
        )
        assert r.status_code == 400


# ── GET /api/blog/image/likes ────────────────────────────


class TestGetLikes:
    def test_returns_5_default_unliked(self):
        sid = _seed_session()
        try:
            r = client.get(f"/api/blog/image/likes?session_id={sid}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert "likes" in body
            assert len(body["likes"]) == 5
            for i, like in enumerate(body["likes"]):
                assert like["image_index"] == i
                assert like["liked"] is False
            # module 매핑 확인
            assert body["likes"][0]["module"] == 1
            assert body["likes"][4]["module"] == 11
        finally:
            _cleanup_likes(sid)

    def test_reflects_existing_likes(self):
        sid = _seed_session()
        try:
            client.post("/api/blog/image/like",
                        json={"session_id": sid, "image_index": 1, "liked": True})
            client.post("/api/blog/image/like",
                        json={"session_id": sid, "image_index": 3, "liked": True})
            r = client.get(f"/api/blog/image/likes?session_id={sid}")
            body = r.json()
            assert body["likes"][0]["liked"] is False
            assert body["likes"][1]["liked"] is True
            assert body["likes"][2]["liked"] is False
            assert body["likes"][3]["liked"] is True
            assert body["likes"][4]["liked"] is False
        finally:
            _cleanup_likes(sid)

    def test_other_clinic_blocked_403(self):
        sid = _seed_session(clinic_id=99)
        try:
            r = client.get(f"/api/blog/image/likes?session_id={sid}")
            assert r.status_code == 403
        finally:
            _cleanup_likes(sid)

    def test_unknown_session_404(self):
        r = client.get(f"/api/blog/image/likes?session_id={uuid.uuid4()}")
        assert r.status_code == 404

    def test_invalid_uuid_400(self):
        r = client.get("/api/blog/image/likes?session_id=not-a-uuid")
        assert r.status_code == 400
