"""
metadata_generator.py — 블로그 메타데이터 생성기 (Phase 3, 2026-04-30)

목적:
  본문은 Sonnet 4.6이 작성, 메타(SEO 제목·태그·요약·OG 설명)는 Haiku 4.5로 위임.
  Haiku는 Sonnet 대비 1/3 비용 + 90% 품질로 메타 추출에 충분.

비용 비교 (1 블로그당):
  Sonnet 본문 (~3000 token output) ≈ ₩170~250
  Haiku 메타  (~200 token output)  ≈ ₩2~5
  → 메타에 Sonnet 쓰면 ₩30~50 추가, Haiku로 -85% 절감

추출 항목:
  - title:        SEO 친화 제목 (40자 이내, 키워드 포함)
  - tags:         태그 5개 (네이버 #해시태그)
  - summary:      150자 요약 (목록·미리보기용)
  - og_description: OG meta description (120자 이내, 클릭 유도)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from ai_client import AIClientError, call_anthropic_messages
from cost_logger import record_cost
from pricing import calculate_anthropic_cost

logger = logging.getLogger(__name__)


HAIKU_MODEL = "claude-haiku-4-5-20251001"
# pricing.py 의 ANTHROPIC_PRICES 키와 매핑 — date suffix 제거한 형태.
HAIKU_PRICING_KEY = "claude-haiku-4-5"


# ── 결과 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class BlogMetadata:
    title: str
    tags: list[str]
    summary: str
    og_description: str
    raw_response: str = ""  # 디버깅·로깅용


# ── 시스템 프롬프트 ───────────────────────────────────────


_SYSTEM_PROMPT = """당신은 한의원 블로그 SEO 메타데이터 추출 전문가입니다.
입력된 블로그 본문을 분석하여 네이버·구글 검색 친화 메타 4종을 JSON으로 반환하세요.

출력 규칙 (반드시 준수):
1. JSON only — 설명·서론·코드블록 금지. 순수 JSON 객체만.
2. title: 40자 이내, 핵심 키워드를 앞쪽에 배치, 후킹 요소(숫자/질문/감정) 1개 포함.
3. tags: 정확히 5개. 한글 명사 위주, 너무 일반적인 단어(한의원·치료) 1개만.
4. summary: 150자 이내, 본문 핵심 1~2문장 요약. "이 글에서는" 같은 메타 표현 금지.
5. og_description: 120자 이내, 클릭 유도형. summary와 다른 각도여야 함.

JSON 스키마:
{
  "title": "...",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "summary": "...",
  "og_description": "..."
}
"""


# ── 핵심 함수 ─────────────────────────────────────────────


def _extract_json(raw: str) -> dict:
    """Haiku 응답에서 JSON 블록 추출. 코드펜스·서론 등 노이즈 제거."""
    # 1. 코드펜스 제거 (```json ... ``` 패턴)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    # 2. 첫 { 부터 마지막 } 까지 (서론·후기 노이즈 제거)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSON 객체를 찾을 수 없습니다.")
    return json.loads(raw[start : end + 1])


def _validate_meta(data: dict) -> None:
    """필수 필드·타입·길이 검증. 호출자가 except 가능."""
    required = {"title", "tags", "summary", "og_description"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"필수 필드 누락: {missing}")

    if not isinstance(data["title"], str) or not data["title"].strip():
        raise ValueError("title은 비어있지 않은 문자열이어야 합니다.")
    if not isinstance(data["tags"], list) or not all(
        isinstance(t, str) for t in data["tags"]
    ):
        raise ValueError("tags는 문자열 리스트여야 합니다.")
    if not isinstance(data["summary"], str):
        raise ValueError("summary는 문자열이어야 합니다.")
    if not isinstance(data["og_description"], str):
        raise ValueError("og_description은 문자열이어야 합니다.")


def generate_metadata(
    blog_text: str,
    keyword: str = "",
    seo_keywords: list[str] | None = None,
    *,
    clinic_id: Optional[int] = None,
    blog_session_id: Optional[str] = None,
) -> BlogMetadata:
    """블로그 본문에서 메타데이터 4종 추출 (Haiku 4.5).

    Args:
        blog_text: 블로그 본문 마크다운.
        keyword: 블로그 주제 (있으면 user_message에 힌트로 포함).
        seo_keywords: 사용자 지정 SEO 키워드 (있으면 tags에 포함되도록 유도).
        clinic_id: 비용 추적용 — 전달 시 cost_logs INSERT (Commit 5a).
                   None 이면 비용 기록 스킵 (테스트·관리 호출 안전).
        blog_session_id: 본문/메타 1편 묶음 ID. cost_logs.blog_session_id 에 저장.

    Returns:
        BlogMetadata.

    Raises:
        AIClientError: API 호출 실패 시 (호출자가 fallback 처리).
        ValueError: Haiku 응답이 유효한 JSON·스키마 아닐 때.
    """
    if not blog_text or not blog_text.strip():
        raise AIClientError("bad_request", "블로그 본문이 비어 있습니다.")

    # 본문이 너무 길면 앞 4000자 + 끝 1000자만 사용 (Haiku 입력 토큰 절약)
    if len(blog_text) > 5000:
        snippet = blog_text[:4000] + "\n\n[...중략...]\n\n" + blog_text[-1000:]
    else:
        snippet = blog_text

    user_parts = []
    if keyword:
        user_parts.append(f"블로그 주제: {keyword}")
    if seo_keywords:
        user_parts.append(
            "사용자가 지정한 SEO 키워드 (tags에 우선 포함): "
            + ", ".join(seo_keywords)
        )
    user_parts.append("아래 본문에서 메타데이터 4종을 JSON으로 추출하세요.")
    user_parts.append("---")
    user_parts.append(snippet)

    user_message = "\n\n".join(user_parts)

    response = call_anthropic_messages(
        model=HAIKU_MODEL,
        system=_SYSTEM_PROMPT,
        user=user_message,
        max_tokens=400,
        cache_system=True,  # 시스템 프롬프트는 동일 구조 → 캐시 적중
    )

    # 비용 기록 (Commit 5a) — clinic_id 있을 때만. fail-soft, 본 흐름 차단 금지.
    if clinic_id is not None:
        usage = response.usage or {}
        tin = int(usage.get("input_tokens", 0) or 0)
        tout = int(usage.get("output_tokens", 0) or 0)
        cr = int(usage.get("cache_read_tokens", 0) or 0)
        cw = int(usage.get("cache_create_tokens", 0) or 0)
        try:
            cost = calculate_anthropic_cost(
                HAIKU_PRICING_KEY,
                tokens_in=tin,
                tokens_out=tout,
                cache_read=cr,
                cache_create=cw,
            )
        except ValueError:
            cost = 0.0
        record_cost(
            kind="anthropic_meta",
            clinic_id=clinic_id,
            cost_usd=cost,
            model=HAIKU_MODEL,
            tokens_in=tin,
            tokens_out=tout,
            cache_read=cr,
            cache_create=cw,
            blog_session_id=blog_session_id,
            metadata={"keyword": keyword} if keyword else None,
        )

    raw = response.content
    try:
        data = _extract_json(raw)
        _validate_meta(data)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("metadata_generator: JSON 파싱 실패. raw=%s", raw[:200])
        raise ValueError(f"Haiku 메타 응답 파싱 실패: {exc}") from exc

    # 길이 가드 (Haiku가 가끔 초과)
    title = data["title"].strip()[:60]
    summary = data["summary"].strip()[:200]
    og_description = data["og_description"].strip()[:160]
    tags = [t.strip() for t in data["tags"] if isinstance(t, str) and t.strip()][:7]

    return BlogMetadata(
        title=title,
        tags=tags,
        summary=summary,
        og_description=og_description,
        raw_response=raw,
    )


def generate_metadata_safe(
    blog_text: str,
    keyword: str = "",
    seo_keywords: list[str] | None = None,
    *,
    clinic_id: Optional[int] = None,
    blog_session_id: Optional[str] = None,
) -> BlogMetadata | None:
    """generate_metadata fail-soft 버전. 실패 시 None 반환 (서비스 영향 없음).

    main.py의 SSE done 이벤트에 끼워넣을 때는 이 버전을 권장.
    """
    try:
        return generate_metadata(
            blog_text,
            keyword,
            seo_keywords,
            clinic_id=clinic_id,
            blog_session_id=blog_session_id,
        )
    except (AIClientError, ValueError) as exc:
        logger.warning("metadata_generator: 메타 생성 실패 (fail-soft): %s", exc)
        return None
