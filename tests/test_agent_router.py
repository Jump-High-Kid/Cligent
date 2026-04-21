import pytest
from src.agent_router import AgentRouter


@pytest.fixture
def router():
    return AgentRouter(agents_dir="agents/runtime", prompts_dir="prompts/agents")


def test_load_agents(router):
    agents = router.get_all_agents()
    assert "blog-agent" in agents
    assert "crm-agent" in agents


def test_classify_blog_intent(router):
    assert router.classify_intent("블로그 초안 써줘") == "blog-agent"


def test_classify_crm_intent(router):
    assert router.classify_intent("환자 재방문 유도 방법 알려줘") == "crm-agent"


def test_classify_inventory_intent(router):
    assert router.classify_intent("약재 재고 확인") == "inventory-agent"


def test_classify_unknown_intent(router):
    assert router.classify_intent("오늘 날씨 어때") is None


def test_get_available_agents_for_role(router):
    available = router.get_available_agents(role="team_member")
    names = [a["name"] for a in available]
    assert "blog-agent" in names
    assert "legal-advisor-agent" not in names


def test_get_available_agents_director(router):
    available = router.get_available_agents(role="director")
    names = [a["name"] for a in available]
    assert "legal-advisor-agent" in names
    assert "tax-advisor-agent" in names


def test_agent_list_no_internal_fields(router):
    """보안: 에이전트 목록에 prompt_file, keywords 미포함"""
    available = router.get_available_agents(role="chief_director")
    for agent in available:
        assert "prompt_file" not in agent
        assert "keywords" not in agent


def test_classify_intent_prefers_most_keyword_matches(router):
    """'약재 재고 확인해줘' → inventory-agent (키워드 2개 매칭)"""
    result = router.classify_intent("약재 재고 확인해줘")
    assert result == "inventory-agent"


def test_path_traversal_rejected(router):
    """보안: 등록되지 않은 agent_name은 ValueError"""
    with pytest.raises(ValueError):
        router.get_agent_config("../../.env")


def test_missing_system_prompt_raises_value_error(router):
    """시스템 프롬프트 파일 없을 때 ValueError (500 아님)"""
    with pytest.raises(ValueError):
        router.get_system_prompt("nonexistent-agent")


def test_invalid_yaml_skipped_gracefully(tmp_path):
    """잘못된 YAML 파일이 있어도 앱이 크래시 안 함"""
    bad_yaml = tmp_path / "bad-agent.yaml"
    bad_yaml.write_text(": invalid: yaml: content: [[[", encoding="utf-8")
    router = AgentRouter(agents_dir=str(tmp_path), prompts_dir="prompts/agents")
    assert isinstance(router.get_all_agents(), dict)
