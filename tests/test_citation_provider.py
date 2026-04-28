"""citation_provider 단위 테스트"""
import pytest
from urllib.parse import quote, unquote
from citation_provider import get_static_citation, get_dynamic_citations, build_citation_block


DIVERSITY_CONFIG = {
    "enabled": True,
    "citations": {
        "static_pool": ["동의보감", "황제내경", "본초강목", "한의학회지", "대한침구학회지", "대한한의학회지"],
        "static_pick_count": 1,
        "dynamic_search": {
            "enabled": True,
            "providers": ["riss", "kci"],
            "pick_count": 2,
            "mode": "link_only",
        },
    },
}


def test_static_citation_returns_from_pool():
    cit = get_static_citation(DIVERSITY_CONFIG)
    assert cit is not None
    pool = DIVERSITY_CONFIG["citations"]["static_pool"]
    assert cit["label"] in pool
    assert cit["url"] is None


def test_static_citation_all_pool_items_reachable():
    seen = set()
    for _ in range(200):
        cit = get_static_citation(DIVERSITY_CONFIG)
        seen.add(cit["label"])
    assert seen == set(DIVERSITY_CONFIG["citations"]["static_pool"])


def test_static_citation_empty_pool_returns_none():
    cfg = {"citations": {"static_pool": []}}
    assert get_static_citation(cfg) is None


def test_dynamic_citations_riss_kci_urls():
    keyword = "허리통증 침치료"
    cits = get_dynamic_citations(keyword, DIVERSITY_CONFIG)
    assert len(cits) == 2
    urls = [c["url"] for c in cits]
    assert any("riss.kr" in u for u in urls)
    assert any("kci.go.kr" in u for u in urls)


def test_dynamic_citations_korean_url_encoded():
    keyword = "소화불량 한방 치료"
    cits = get_dynamic_citations(keyword, DIVERSITY_CONFIG)
    encoded = quote(keyword)
    for cit in cits:
        assert encoded in cit["url"]


def test_dynamic_citations_disabled():
    cfg = {**DIVERSITY_CONFIG, "citations": {**DIVERSITY_CONFIG["citations"], "dynamic_search": {"enabled": False}}}
    assert get_dynamic_citations("테스트", cfg) == []


def test_build_citation_block_contains_riss_kci():
    block = build_citation_block("허리통증", DIVERSITY_CONFIG)
    assert "riss.kr" in block
    assert "kci.go.kr" in block
    assert "참고 문헌" in block


def test_build_citation_block_no_effectiveness_claim():
    block = build_citation_block("목통증 한약치료", DIVERSITY_CONFIG)
    assert "효과 있다" not in block
    assert "효과가 입증" not in block


def test_build_citation_block_empty_config_returns_empty():
    block = build_citation_block("테스트", {"citations": {"static_pool": [], "dynamic_search": {"enabled": False}}})
    assert block == ""
