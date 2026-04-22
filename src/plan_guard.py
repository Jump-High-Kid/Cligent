"""
plan_guard.py — FastAPI Depends()로 사용하는 블로그 생성 한도 체크

체크 우선순위:
  1. plan_expires_at 미래  → 유료 플랜 활성 → 통과
  2. trial_expires_at 미래 → 체험 플랜 활성 → 통과
  3. 둘 다 과거/NULL       → 무료 플랜 → 월 3편 한도 체크

DB 장애 전략:
  - 60초 TTL 메모리 캐시(최대 500 클리닉)에서 먼저 확인
  - 캐시도 없으면 fail open (유료 유저 차단 > 무료 유저 비용 노출)

trial abuse 방어:
  - trial_expires_at은 signup 시 1회만 설정, 이 모듈에서 재설정 경로 없음
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# 플랜 정보 캐시: clinic_id → (plan_data_dict, expires_at_timestamp)
_plan_cache: Dict[int, Tuple[dict, float]] = {}
_CACHE_TTL = 60      # 초
_MAX_CACHE = 500     # 최대 클리닉 수
_FREE_BLOG_LIMIT = 3  # 무료 플랜 월 생성 한도


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_cached(clinic_id: int) -> Optional[dict]:
    """캐시에서 플랜 정보 반환. 만료됐거나 없으면 None."""
    entry = _plan_cache.get(clinic_id)
    if entry is None:
        return None
    data, exp = entry
    if time.monotonic() < exp:
        return data
    del _plan_cache[clinic_id]
    return None


def _set_cache(clinic_id: int, data: dict) -> None:
    if len(_plan_cache) >= _MAX_CACHE:
        # 가장 만료 시각이 이른 항목 제거
        oldest = min(_plan_cache, key=lambda k: _plan_cache[k][1])
        _plan_cache.pop(oldest, None)
    _plan_cache[clinic_id] = (data, time.monotonic() + _CACHE_TTL)


def invalidate_plan_cache(clinic_id: int) -> None:
    """플랜 변경(결제·해지) 시 캐시 무효화."""
    _plan_cache.pop(clinic_id, None)


def _fetch_plan_data(clinic_id: int) -> Optional[dict]:
    """
    DB에서 플랜 정보 조회 후 캐시 저장.
    DB 장애 시 None 반환 (호출자가 fail open 처리).
    """
    cached = _get_cached(clinic_id)
    if cached is not None:
        return cached
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT plan_id, plan_expires_at, trial_expires_at FROM clinics WHERE id = ?",
                (clinic_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        _set_cache(clinic_id, data)
        return data
    except Exception as exc:
        logger.warning("plan_guard: DB 조회 실패 (clinic_id=%s): %s", clinic_id, exc)
        return None


def _count_monthly_blogs(clinic_id: int) -> int:
    """
    이번 달 블로그 생성 횟수. DB 장애 시 -1 반환 (호출자가 fail open 처리).
    idx_usage_logs_clinic_month 인덱스를 활용하는 쿼리.
    """
    try:
        from db_manager import get_db
        # 이번 달 1일 00:00:00 UTC 기준
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM usage_logs
                WHERE clinic_id = ?
                  AND feature = 'blog_generation'
                  AND used_at >= ?
                """,
                (clinic_id, month_start),
            ).fetchone()
        return row["cnt"] if row else 0
    except Exception as exc:
        logger.warning("plan_guard: 사용량 조회 실패 (clinic_id=%s): %s", clinic_id, exc)
        return -1


def check_blog_limit(clinic_id: int) -> None:
    """
    블로그 생성 전 한도 체크. HTTPException(429) 발생 시 생성 차단.
    main.py /generate 엔드포인트에서 직접 호출.

    사용 예:
        check_blog_limit(user["clinic_id"])
    """
    now = _now_iso()

    plan_data = _fetch_plan_data(clinic_id)

    if plan_data is None:
        # DB 장애 + 캐시 miss → fail open (유료 유저 차단 방지 우선)
        logger.warning("plan_guard: 플랜 정보 없음, fail open (clinic_id=%s)", clinic_id)
        return

    # 1. 유료 플랜 체크
    plan_expires_at = plan_data.get("plan_expires_at")
    if plan_expires_at and plan_expires_at > now:
        return  # 유료 플랜 활성

    # 2. 체험 플랜 체크 (trial_expires_at은 signup 시 1회만 설정)
    trial_expires_at = plan_data.get("trial_expires_at")
    if trial_expires_at and trial_expires_at > now:
        return  # 체험 플랜 활성

    # 3. 무료 플랜 → 월 한도 체크
    count = _count_monthly_blogs(clinic_id)
    if count < 0:
        # 사용량 조회 실패 → fail open
        return

    if count >= _FREE_BLOG_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "plan_limit_exceeded",
                "message": (
                    f"이번 달 무료 플랜 한도({_FREE_BLOG_LIMIT}편)에 도달했습니다. "
                    "스탠다드 플랜으로 업그레이드하면 제한 없이 생성할 수 있습니다."
                ),
                "current": count,
                "limit": _FREE_BLOG_LIMIT,
            },
        )
