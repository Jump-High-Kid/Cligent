"""
test_image_routes.py — Phase 4 이미지 라우트 통합 테스트

검증 대상:
  POST /api/image/generate-initial   — 5장 생성 + 세션 발급
  POST /api/image/regenerate         — 한도 내 통과 / 초과 시 429
  POST /api/image/edit               — multipart 업로드, 한도 내/초과
  GET  /api/image/session/{id}       — 세션 상태 조회

OpenAI 호출은 ai_client 함수 직접 모킹 (실호출 금지).
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")

from main import app  # noqa: E402
from auth_manager import get_current_user  # noqa: E402
from ai_client import AIResponse  # noqa: E402

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


@pytest.fixture(autouse=True)
def patch_plan(monkeypatch):
    """plan_guard.get_effective_plan을 'standard' 반환으로 고정."""
    import plan_guard

    monkeypatch.setattr(
        plan_guard,
        "get_effective_plan",
        lambda clinic_id: {
            "plan_id": "standard",
            "is_paid": True,
            "is_trial": False,
            "has_unlimited": True,
            "trial_days_left": None,
        },
    )


def _fake_responses(n: int) -> list[AIResponse]:
    return [
        AIResponse(content=f"BASE64_{i}", usage={"mode": "test"})
        for i in range(n)
    ]


# ── /api/image/generate-initial ───────────────────────────


class TestGenerateInitial:
    def test_creates_session_and_returns_5_images(self):
        with patch(
            "image_generator.call_openai_image_generate",
            return_value=_fake_responses(5),
        ):
            res = client.post(
                "/api/image/generate-initial",
                json={"prompt": "knee diagram", "keyword": "허리디스크"},
            )

        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert len(data["images"]) == 5
        assert data["size"] == "1024x1024"
        assert data["quality"] == "medium"
        assert data["plan_id"] == "standard"
        assert data["quota"]["regen"]["remaining"] == 1
        assert data["quota"]["edit"]["remaining"] == 2

    def test_empty_prompt_rejected(self):
        res = client.post("/api/image/generate-initial", json={"prompt": ""})
        assert res.status_code == 400


# ── /api/image/regenerate ─────────────────────────────────


class TestRegenerate:
    def _create_session(self) -> str:
        with patch(
            "image_generator.call_openai_image_generate",
            return_value=_fake_responses(5),
        ):
            res = client.post(
                "/api/image/generate-initial",
                json={"prompt": "knee", "keyword": "x"},
            )
        return res.json()["session_id"]

    def test_first_regen_allowed_for_standard(self):
        sid = self._create_session()
        with patch(
            "image_generator.call_openai_image_generate",
            return_value=_fake_responses(5),
        ):
            res = client.post(
                "/api/image/regenerate",
                json={"session_id": sid, "prompt": "knee diagram revised"},
            )
        assert res.status_code == 200
        data = res.json()
        assert len(data["images"]) == 5
        assert data["quota"]["regen"]["used"] == 1
        assert data["quota"]["regen"]["remaining"] == 0

    def test_second_regen_blocked_429(self):
        sid = self._create_session()
        # 첫 regen 통과
        with patch(
            "image_generator.call_openai_image_generate",
            return_value=_fake_responses(5),
        ):
            client.post(
                "/api/image/regenerate",
                json={"session_id": sid, "prompt": "v2"},
            )
        # 두 번째는 한도 초과
        res = client.post(
            "/api/image/regenerate",
            json={"session_id": sid, "prompt": "v3"},
        )
        assert res.status_code == 429
        detail = res.json()["detail"]
        assert detail["kind"] == "quota_exceeded"
        assert detail["type"] == "regen"

    def test_unknown_session_404(self):
        res = client.post(
            "/api/image/regenerate",
            json={"session_id": "nonexistent", "prompt": "x"},
        )
        assert res.status_code == 404

    def test_other_clinic_session_403(self, monkeypatch):
        """다른 clinic_id 세션은 403."""
        from image_session_manager import create_session

        sid = create_session(
            clinic_id=999, user_id=999, plan_id_at_start="standard"
        )
        res = client.post(
            "/api/image/regenerate",
            json={"session_id": sid, "prompt": "x"},
        )
        assert res.status_code == 403


# ── /api/image/edit ──────────────────────────────────────


class TestEdit:
    def _create_session(self) -> str:
        with patch(
            "image_generator.call_openai_image_generate",
            return_value=_fake_responses(5),
        ):
            res = client.post(
                "/api/image/generate-initial",
                json={"prompt": "knee", "keyword": "x"},
            )
        return res.json()["session_id"]

    def test_first_edit_allowed(self):
        sid = self._create_session()
        with patch(
            "image_generator.call_openai_image_edit",
            return_value=_fake_responses(1),
        ):
            res = client.post(
                "/api/image/edit",
                data={"session_id": sid, "prompt": "신유혈 강조"},
                files={"image": ("img.png", io.BytesIO(b"PNG_RAW"), "image/png")},
            )
        assert res.status_code == 200
        data = res.json()
        assert len(data["images"]) == 1
        assert data["quota"]["edit"]["used"] == 1
        assert data["quota"]["edit"]["remaining"] == 1

    def test_third_edit_blocked_429(self):
        sid = self._create_session()
        # standard = edit_free 2 → 첫 2회 통과, 3회째 차단
        with patch(
            "image_generator.call_openai_image_edit",
            return_value=_fake_responses(1),
        ):
            for _ in range(2):
                r = client.post(
                    "/api/image/edit",
                    data={"session_id": sid, "prompt": "x"},
                    files={"image": ("img.png", io.BytesIO(b"PNG"), "image/png")},
                )
                assert r.status_code == 200

        res = client.post(
            "/api/image/edit",
            data={"session_id": sid, "prompt": "x"},
            files={"image": ("img.png", io.BytesIO(b"PNG"), "image/png")},
        )
        assert res.status_code == 429
        assert res.json()["detail"]["type"] == "edit"

    def test_missing_image_400(self):
        sid = self._create_session()
        res = client.post(
            "/api/image/edit",
            data={"session_id": sid, "prompt": "x"},
        )
        assert res.status_code == 400


# ── GET 세션 상태 ────────────────────────────────────────


class TestSessionStatus:
    def test_returns_quota(self):
        with patch(
            "image_generator.call_openai_image_generate",
            return_value=_fake_responses(5),
        ):
            res = client.post(
                "/api/image/generate-initial",
                json={"prompt": "knee", "keyword": "디스크"},
            )
        sid = res.json()["session_id"]

        res2 = client.get(f"/api/image/session/{sid}")
        assert res2.status_code == 200
        data = res2.json()
        assert data["session_id"] == sid
        assert data["plan_id"] == "standard"
        assert data["regen_count"] == 0
        assert data["edit_count"] == 0
        assert data["quota"]["regen"]["remaining"] == 1

    def test_unknown_session_404(self):
        res = client.get("/api/image/session/no-such-id")
        assert res.status_code == 404


# ── K-8 (2026-05-04): 누적 이미지 세션 한도 ──────────────────────────────


class TestImageSessionLimitK8:
    """K-8: 어뷰저의 generate-initial 무한 호출 차단 — free/trial 누적 한도."""

    def test_free_plan_at_limit_returns_429(self, monkeypatch):
        """free 플랜 + 누적 30 → 429 (image_session_limit_exceeded)."""
        import plan_guard

        monkeypatch.setattr(
            plan_guard,
            "_fetch_plan_data",
            lambda cid: {
                "plan_id": "free",
                "plan_expires_at": None,
                "trial_expires_at": None,
            },
        )
        monkeypatch.setattr(
            plan_guard,
            "_count_total_image_sessions",
            lambda cid: plan_guard._IMAGE_SESSION_LIMIT,
        )

        res = client.post(
            "/api/image/generate-initial",
            json={"prompt": "knee", "keyword": "x"},
        )
        assert res.status_code == 429
        detail = res.json()["detail"]
        assert detail["error"] == "image_session_limit_exceeded"
        assert detail["limit"] == plan_guard._IMAGE_SESSION_LIMIT

    def test_paid_plan_exceeds_limit_still_allowed(self, monkeypatch):
        """유료 플랜은 누적 카운트 무관 — generate-initial 정상 동작."""
        import plan_guard
        from datetime import datetime, timedelta, timezone

        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        monkeypatch.setattr(
            plan_guard,
            "_fetch_plan_data",
            lambda cid: {
                "plan_id": "standard",
                "plan_expires_at": future,
                "trial_expires_at": None,
            },
        )
        monkeypatch.setattr(
            plan_guard,
            "_count_total_image_sessions",
            lambda cid: 99999,
        )

        with patch(
            "image_generator.call_openai_image_generate",
            return_value=_fake_responses(5),
        ):
            res = client.post(
                "/api/image/generate-initial",
                json={"prompt": "knee", "keyword": "x"},
            )
        assert res.status_code == 200
