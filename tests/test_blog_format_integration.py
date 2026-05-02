"""
test_blog_format_integration.py — v0.3 다양성 레이어 통합 테스트

실제 Anthropic API 호출 없이 format/hook/citation이 blog_generator 파이프라인에
올바르게 주입되는지 검증한다.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import os
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


def make_stream_mock(chunks=None):
    chunks = chunks or ["테스트 블로그 내용입니다."]
    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_stream.text_stream = iter(chunks)
    final_msg = MagicMock()
    final_msg.usage.input_tokens = 100
    final_msg.usage.output_tokens = 50
    mock_stream.get_final_message = MagicMock(return_value=final_msg)
    return mock_stream


def parse_sse(content: bytes) -> list[dict]:
    events = []
    for line in content.decode("utf-8").splitlines():
        if line.startswith("data: "):
            raw = line[6:].strip()
            if raw:
                events.append(json.loads(raw))
    return events


class TestFormatInjection:
    def test_specific_format_id_returned_in_done_event(self):
        """format_id 지정 시 done 이벤트에 format_id가 반환된다."""
        mock_stream = make_stream_mock()
        with patch("blog_generator.anthropic.Anthropic") as mock_cls, \
             patch("routers.blog.check_blog_limit"), \
             patch("main.os.getenv", return_value="sk-ant-test"):
            mock_cls.return_value.messages.stream.return_value = mock_stream
            res = client.post("/generate", json={
                "keyword": "허리통증 한방 치료",
                "answers": {},
                "format_id": "qna",
            })

        events = parse_sse(res.content)
        done = next((e for e in events if e.get("done")), None)
        assert done is not None
        assert done.get("format_id") == "qna"

    def test_auto_format_returns_valid_format_id(self):
        """format_id 미지정 시 done 이벤트에 유효한 format_id가 반환된다."""
        valid_ids = {"information", "case_study", "qna", "comparison", "seasonal", "lifestyle"}
        mock_stream = make_stream_mock()
        with patch("blog_generator.anthropic.Anthropic") as mock_cls, \
             patch("routers.blog.check_blog_limit"), \
             patch("main.os.getenv", return_value="sk-ant-test"):
            mock_cls.return_value.messages.stream.return_value = mock_stream
            res = client.post("/generate", json={
                "keyword": "소화불량 한방 치료",
                "answers": {},
            })

        events = parse_sse(res.content)
        done = next((e for e in events if e.get("done")), None)
        assert done is not None
        assert done.get("format_id") in valid_ids

    def test_hook_id_returned_in_done_event(self):
        """done 이벤트에 hook_id가 포함돼야 한다."""
        valid_hooks = {"statistic", "case", "question", "season", "classic_quote"}
        mock_stream = make_stream_mock()
        with patch("blog_generator.anthropic.Anthropic") as mock_cls, \
             patch("routers.blog.check_blog_limit"), \
             patch("main.os.getenv", return_value="sk-ant-test"):
            mock_cls.return_value.messages.stream.return_value = mock_stream
            res = client.post("/generate", json={
                "keyword": "두통 침치료",
                "answers": {},
            })

        events = parse_sse(res.content)
        done = next((e for e in events if e.get("done")), None)
        assert done is not None
        assert done.get("hook_id") in valid_hooks


class TestSystemPromptInjection:
    def test_format_template_in_system_prompt(self):
        """format=qna 선택 시 시스템 프롬프트에 'Q&A' 관련 지시가 포함된다."""
        captured = {}

        def fake_stream(*args, **kwargs):
            captured["system"] = kwargs.get("system", "")
            return make_stream_mock()

        with patch("blog_generator.anthropic.Anthropic") as mock_cls, \
             patch("routers.blog.check_blog_limit"), \
             patch("main.os.getenv", return_value="sk-ant-test"):
            mock_cls.return_value.messages.stream.side_effect = fake_stream
            client.post("/generate", json={
                "keyword": "소화불량",
                "answers": {},
                "format_id": "qna",
            })

        assert "Q&A" in captured.get("system", "") or "이번 글 형식" in captured.get("system", "")

    def test_case_study_override_in_system_prompt(self):
        """case_study 선택 시 시스템 프롬프트에 형식 재정의 지시가 포함된다."""
        captured = {}

        def fake_stream(*args, **kwargs):
            captured["system"] = kwargs.get("system", "")
            return make_stream_mock()

        with patch("blog_generator.anthropic.Anthropic") as mock_cls, \
             patch("routers.blog.check_blog_limit"), \
             patch("main.os.getenv", return_value="sk-ant-test"):
            mock_cls.return_value.messages.stream.side_effect = fake_stream
            client.post("/generate", json={
                "keyword": "허리디스크",
                "answers": {},
                "format_id": "case_study",
            })

        system = captured.get("system", "")
        assert "형식 재정의" in system or "가명" in system

    def test_citation_block_appended_to_output(self):
        """citation 블록이 replace 이벤트로 본문 끝에 추가된다."""
        mock_stream = make_stream_mock(["본문 내용"])
        with patch("blog_generator.anthropic.Anthropic") as mock_cls, \
             patch("routers.blog.check_blog_limit"), \
             patch("main.os.getenv", return_value="sk-ant-test"):
            mock_cls.return_value.messages.stream.return_value = mock_stream
            res = client.post("/generate", json={
                "keyword": "침치료 효과",
                "answers": {},
            })

        events = parse_sse(res.content)
        # citation은 replace 이벤트로 전송됨
        replace_events = [e for e in events if "replace" in e]
        assert len(replace_events) > 0, "replace 이벤트가 없음 — citation이 append되지 않았을 수 있음"
        final_text = replace_events[-1]["replace"]
        assert "riss.kr" in final_text or "kci.go.kr" in final_text or "참고 문헌" in final_text


class TestBuildPromptEndpoint:
    def test_build_prompt_returns_format_and_hook(self):
        """/build-prompt 응답에 format_id, hook_id가 포함된다."""
        valid_ids = {"information", "case_study", "qna", "comparison", "seasonal", "lifestyle"}
        valid_hooks = {"statistic", "case", "question", "season", "classic_quote"}

        res = client.post("/build-prompt", json={
            "keyword": "소화불량",
            "answers": {},
        })

        assert res.status_code == 200
        data = res.json()
        assert data.get("format_id") in valid_ids
        assert data.get("hook_id") in valid_hooks

    def test_build_prompt_with_fixed_format(self):
        """/build-prompt에 format_id 지정 시 해당 format이 반환된다."""
        res = client.post("/build-prompt", json={
            "keyword": "두통",
            "answers": {},
            "format_id": "comparison",
        })

        assert res.status_code == 200
        data = res.json()
        assert data.get("format_id") == "comparison"
