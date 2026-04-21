import pytest
import json
from src.agent_middleware import AgentMiddleware


@pytest.fixture
def middleware(tmp_path):
    log_path = tmp_path / "agent_log.jsonl"
    return AgentMiddleware(log_path=str(log_path))


def test_log_request(middleware, tmp_path):
    middleware.log_request(
        user_id="user1",
        agent_name="blog-agent",
        message="블로그 써줘",
        input_tokens=100,
        output_tokens=500,
    )
    log_path = tmp_path / "agent_log.jsonl"
    assert log_path.exists()
    entry = json.loads(log_path.read_text().strip())
    assert entry["agent_name"] == "blog-agent"
    assert entry["input_tokens"] == 100


def test_calculate_cost(middleware):
    # claude-sonnet-4-6: input $3/M, output $15/M
    cost = middleware.calculate_cost(input_tokens=1000, output_tokens=500)
    assert abs(cost["input_krw"] - 3 * 1000 / 1_000_000 * 1350) < 0.01
    assert abs(cost["output_krw"] - 15 * 500 / 1_000_000 * 1350) < 0.01


def test_check_medical_hallucination_risk(middleware):
    # 기존 패턴
    assert middleware.check_hallucination_risk("이 치료법은 반드시 효과가 있습니다") is True
    assert middleware.check_hallucination_risk("100% 치료됩니다") is True
    assert middleware.check_hallucination_risk("완치 보장합니다") is True
    assert middleware.check_hallucination_risk("부작용 없음이 확인됐습니다") is True
    assert middleware.check_hallucination_risk("임상적으로 증명된 방법입니다") is True

    # 수치 + 효과 조합
    assert middleware.check_hallucination_risk("연구에서 85% 효과가 확인됐습니다") is True
    assert middleware.check_hallucination_risk("3회 치료 후 완치율 90%") is True
    assert middleware.check_hallucination_risk("치료 성공률 95%입니다") is True

    # 가상 인용
    assert middleware.check_hallucination_risk("김민준 교수 연구팀에 따르면 효과적입니다") is True
    assert middleware.check_hallucination_risk("연구에서 효과가 입증되었습니다") is True
    assert middleware.check_hallucination_risk("임상 연구에서 확실히 검증됐습니다") is True

    # 단정적 치료 표현
    assert middleware.check_hallucination_risk("3회 치료 후 완전 회복됩니다") is True
    assert middleware.check_hallucination_risk("근본 치료가 가능합니다") is True
    assert middleware.check_hallucination_risk("재발 없이 완치됩니다") is True

    # 안전한 표현 — false 여야 함
    assert middleware.check_hallucination_risk("블로그 아이디어 알려줘") is False
    assert middleware.check_hallucination_risk("도움이 될 수 있습니다") is False
    assert middleware.check_hallucination_risk("개선에 도움을 드릴 수 있습니다") is False
    assert middleware.check_hallucination_risk("연구에서 효과가 보고되었습니다") is False


def test_log_does_not_store_raw_message(middleware, tmp_path):
    """PIPA 준수: 원문 메시지 대신 SHA-256 해시값만 저장"""
    middleware.log_request(
        user_id="user1",
        agent_name="blog-agent",
        message="환자 홍길동 010-1234-5678",
        input_tokens=50,
        output_tokens=100,
    )
    log_path = tmp_path / "agent_log.jsonl"
    content = log_path.read_text()
    assert "홍길동" not in content
    assert "010-1234-5678" not in content
    entry = json.loads(content.strip())
    assert "message_hash" in entry
