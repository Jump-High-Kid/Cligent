"""
cost_logger.py — cost_logs 테이블 INSERT 헬퍼 (Commit 5a, 2026-05-04)

설계:
  - 호출자(metadata_generator / blog_generator / routers/blog / blog_chat_flow / admin)는
    pricing.calculate_*() 결과 USD 와 컨텍스트(clinic_id / session_id) 를 넘긴다.
  - 이 모듈이 단일 진실원으로 cost_logs 테이블에 1행 INSERT.
  - JSON 직렬화·DB I/O 실패는 모두 흡수 (fail-soft). 본 흐름 차단 금지.

소비자 (Commit 7 KPI 어드민):
  - SELECT SUM(cost_usd) FROM cost_logs GROUP BY clinic_id, kind, date(created_at)
  - blog_session_id 로 1편 블로그 풀 비용 합산 (본문 1 + 메타 1 + 이미지 5 = 7행)

확장:
  - 새 kind 추가 → VALID_COST_KINDS 추가 + 호출부 wire-up
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Commit 5 합의된 6종 — 각 호출 경로 1:1 매핑
VALID_COST_KINDS: tuple[str, ...] = (
    "anthropic_blog",       # blog_generator.py 본문 Sonnet
    "anthropic_meta",       # metadata_generator.py Haiku
    "openai_image_init",    # 첫 5장 (블로그 1편당 1회)
    "openai_image_regen",   # 재생성
    "openai_image_edit",    # 부분 수정
    "openai_image_admin",   # 어드민 테스트 툴 — KPI 분리
)


def record_cost(
    kind: str,
    clinic_id: int,
    cost_usd: float,
    *,
    model: Optional[str] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read: int = 0,
    cache_create: int = 0,
    blog_session_id: Optional[str] = None,
    image_session_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> bool:
    """cost_logs 테이블에 비용 1행 INSERT.

    Args:
        kind: VALID_COST_KINDS 중 하나.
        clinic_id: clinics.id FK. 위반 시 fail-soft False.
        cost_usd: pricing.calculate_*() 결과. KRW 변환은 어드민 표시 시점.
        model: 모델 식별자 (e.g. "claude-sonnet-4-6", "gpt-image-2").
        tokens_in/out: Anthropic / OpenAI 텍스트 토큰. 이미지 호출은 0.
        cache_read/create: Anthropic prompt caching 토큰.
        blog_session_id: 본문/메타 1편 단위 묶음 ID (UUID4 또는 임의).
        image_session_id: 이미지 세션 ID — image_sessions.session_id.
        metadata: 추가 컨텍스트. JSON 직렬화 가능해야 함.

    Returns:
        True — INSERT 성공.
        False — invalid kind / JSON 직렬화 실패 / DB I/O 실패 (raise 없음).
    """
    if kind not in VALID_COST_KINDS:
        logger.warning("cost_logger: unknown kind=%s clinic_id=%s", kind, clinic_id)
        return False

    # metadata JSON 직렬화는 try 밖에서 수행 — 직렬화 실패도 fail-soft.
    metadata_json: Optional[str] = None
    if metadata is not None:
        try:
            metadata_json = json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.warning("cost_logger: metadata 직렬화 실패 (%s)", e)
            return False

    try:
        # lazy import — db_manager.DB_PATH 가 테스트에서 monkeypatch 되도록
        from db_manager import get_db

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO cost_logs
                    (clinic_id, kind, model,
                     tokens_in, tokens_out, cache_read, cache_create,
                     cost_usd, blog_session_id, image_session_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clinic_id, kind, model,
                    int(tokens_in), int(tokens_out),
                    int(cache_read), int(cache_create),
                    float(cost_usd),
                    blog_session_id, image_session_id, metadata_json,
                ),
            )
        return True
    except Exception as e:
        # FK 위반(IntegrityError) / 경로 없음(OperationalError) / 기타 모두 흡수
        logger.warning(
            "cost_logger: INSERT 실패 kind=%s clinic_id=%s err=%s",
            kind, clinic_id, e,
        )
        return False
