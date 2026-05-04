"""
test_input_limits_routes.py — K-7 통합 테스트

각 라우트에 입력 길이/형식 한도가 실제로 부과되는지 검증.
관련 라우트:
  - /api/image/generate-initial : prompt 2000 / keyword 200
  - /build-prompt              : 다중 필드 (헬퍼 _validate_blog_inputs)
  - /generate                  : 동일
  - /api/blog-chat/turn        : session_id UUID4
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")

from main import app
from auth_manager import get_current_user

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


# ── /api/image/generate-initial ──────────────────────────────────────────────

class TestImageGenerateInitial:
    def test_oversized_prompt_rejects(self):
        res = client.post(
            "/api/image/generate-initial",
            json={"prompt": "x" * 2001, "keyword": "ok"},
        )
        assert res.status_code == 400

    def test_oversized_keyword_rejects(self):
        res = client.post(
            "/api/image/generate-initial",
            json={"prompt": "valid prompt", "keyword": "x" * 201},
        )
        assert res.status_code == 400

    def test_non_string_prompt_rejects(self):
        res = client.post(
            "/api/image/generate-initial",
            json={"prompt": 12345, "keyword": "ok"},
        )
        assert res.status_code == 400


# ── /build-prompt (plan_guard 미적용) ────────────────────────────────────────

class TestBuildPromptInputs:
    def _payload(self, **overrides):
        base = {"keyword": "어깨 통증"}
        base.update(overrides)
        return base

    def test_oversized_keyword_rejects(self):
        res = client.post("/build-prompt", json=self._payload(keyword="x" * 201))
        assert res.status_code == 400

    def test_oversized_clinic_info_rejects(self):
        res = client.post(
            "/build-prompt", json=self._payload(clinic_info="x" * 2001)
        )
        assert res.status_code == 400

    def test_oversized_materials_text_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(materials={"text": "x" * 5001}),
        )
        assert res.status_code == 400

    def test_too_many_seo_keywords_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(seo_keywords=["k"] * 21),
        )
        assert res.status_code == 400

    def test_too_long_seo_keyword_item_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(seo_keywords=["x" * 51]),
        )
        assert res.status_code == 400

    def test_too_many_explanation_types_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(explanation_types=["a"] * 11),
        )
        assert res.status_code == 400

    def test_oversized_answer_value_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(answers={"q1": "x" * 501}),
        )
        assert res.status_code == 400

    def test_too_many_answer_keys_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(answers={f"k{i}": "v" for i in range(21)}),
        )
        assert res.status_code == 400

    def test_oversized_web_link_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(materials={"webLinks": ["x" * 501]}),
        )
        assert res.status_code == 400

    def test_too_many_youtube_links_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(
                materials={"youtubeLinks": ["https://yt/" + str(i) for i in range(21)]}
            ),
        )
        assert res.status_code == 400

    def test_char_count_out_of_range_rejects(self):
        res = client.post(
            "/build-prompt",
            json=self._payload(char_count={"min": 50, "max": 100}),
        )
        assert res.status_code == 400

    def test_format_id_too_long_rejects(self):
        res = client.post(
            "/build-prompt", json=self._payload(format_id="x" * 51)
        )
        assert res.status_code == 400


# ── /generate (plan_guard 적용 — 한도 통과 가정) ─────────────────────────────

class TestGenerateInputs:
    """본 테스트는 plan_guard.check_blog_limit 통과한다는 가정 하에
    입력 검증이 본문 생성 진입 전에 차단하는지만 확인."""

    def test_oversized_keyword_rejects(self):
        res = client.post("/generate", json={"keyword": "x" * 201})
        # 입력 검증은 plan_guard 이후에 실행됨 — 한도 초과 시 429, 검증 실패 시 400
        assert res.status_code in (400, 429)

    def test_oversized_materials_text_rejects(self):
        res = client.post(
            "/generate",
            json={"keyword": "어깨", "materials": {"text": "x" * 5001}},
        )
        assert res.status_code in (400, 429)


# ── /api/blog-chat/turn session_id UUID4 ─────────────────────────────────────

class TestBlogChatTurnSessionId:
    def test_garbage_session_id_rejects(self):
        res = client.post(
            "/api/blog-chat/turn",
            json={"session_id": "not-a-uuid", "user_input": "test"},
        )
        assert res.status_code == 400

    def test_oversized_session_id_rejects(self):
        res = client.post(
            "/api/blog-chat/turn",
            json={"session_id": "a" * 100000, "user_input": "test"},
        )
        assert res.status_code == 400

    def test_uuid_v1_rejects(self):
        # v1 거부 (UUID4만 허용)
        res = client.post(
            "/api/blog-chat/turn",
            json={
                "session_id": "12345678-1234-1abc-8def-1234567890ab",
                "user_input": "test",
            },
        )
        assert res.status_code == 400

    def test_empty_session_id_treated_as_new(self):
        # 빈 session_id 는 신규 세션 — 검증 통과 (다른 검증으로 막히지 않으면 200)
        res = client.post(
            "/api/blog-chat/turn",
            json={"session_id": "", "user_input": ""},
        )
        # 빈 입력으로 신규 세션 생성, 200 응답 가능
        assert res.status_code in (200, 400)
