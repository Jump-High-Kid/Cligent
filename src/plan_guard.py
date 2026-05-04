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

공용 함수:
  resolve_effective_plan() — DB 조회 없는 순수 함수, plan_notify/main.py에서 공유
  get_effective_plan()     — 캐시 활용 버전, clinic_id 기반 조회
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# 플랜 정보 캐시: clinic_id → (plan_data_dict, expires_at_timestamp)
_plan_cache: Dict[int, Tuple[dict, float]] = {}
_CACHE_TTL = 60      # 초
_MAX_CACHE = 500     # 최대 클리닉 수

# 베타 한도는 config.yaml beta: 섹션에서 로드 (코드 수정 없이 변경 가능)
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_beta_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("beta", {}) or {}
    except Exception as exc:
        logger.warning("plan_guard: config.yaml 로드 실패, 기본값 사용 (%s)", exc)
        return {}


_BETA_CFG = _load_beta_config()
_FREE_BLOG_LIMIT = int(_BETA_CFG.get("blog_limit_total", 25))      # 베타 기간 총 생성 한도
_PROMPT_COPY_LIMIT = int(_BETA_CFG.get("prompt_copy_limit", 999))  # 베타 기간 프롬프트 복사 한도
_TRIAL_DAYS = int(_BETA_CFG.get("trial_days", 90))                 # signup 시 trial 기간(일)
# K-8 (2026-05-04): 이미지 세션 누적 한도 — 어뷰저의 generate-initial 무한 호출 차단.
_IMAGE_SESSION_LIMIT = int(_BETA_CFG.get("image_session_limit_total", 30))


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


# ── 공용 순수 함수 ────────────────────────────────────────────────

def resolve_effective_plan(
    plan_id: Optional[str],
    plan_expires_at: Optional[str],
    trial_expires_at: Optional[str],
) -> dict:
    """
    실효 플랜 결정 — DB/캐시 의존 없는 순수 함수.

    plan_guard, plan_notify, main.py 세 곳에서 공유.
    세 곳 모두 동일한 우선순위 로직을 보장한다.

    Returns:
        plan_id: str            — 실효 플랜 (free | trial | standard | pro 등)
        is_paid: bool           — 유료 플랜 활성 여부
        is_trial: bool          — 체험 플랜 활성 여부
        has_unlimited: bool     — 무제한 생성 가능 여부 (paid 또는 trial)
        trial_days_left: int|None — 체험 남은 일수 (is_trial=True 일 때만)
    """
    now = _now_iso()
    raw_plan = plan_id or "free"

    if plan_expires_at and plan_expires_at > now:
        return {
            "plan_id": raw_plan,
            "is_paid": True,
            "is_trial": False,
            "has_unlimited": True,
            "trial_days_left": None,
        }

    if trial_expires_at and trial_expires_at > now:
        days_left = None
        try:
            trial_dt = datetime.fromisoformat(trial_expires_at.replace("Z", "+00:00"))
            delta = trial_dt - datetime.now(timezone.utc)
            days_left = max(0, delta.days)
        except ValueError:
            pass
        return {
            "plan_id": "trial",
            "is_paid": False,
            "is_trial": True,
            "has_unlimited": True,
            "trial_days_left": days_left,
        }

    return {
        "plan_id": "free",
        "is_paid": False,
        "is_trial": False,
        "has_unlimited": False,
        "trial_days_left": None,
    }


def get_effective_plan(clinic_id: int) -> dict:
    """
    캐시를 활용한 실효 플랜 조회. DB 장애 시 free 반환(fail-safe).
    resolve_effective_plan()의 캐시 버전.
    """
    plan_data = _fetch_plan_data(clinic_id)
    if plan_data is None:
        return resolve_effective_plan(None, None, None)
    return resolve_effective_plan(
        plan_data.get("plan_id"),
        plan_data.get("plan_expires_at"),
        plan_data.get("trial_expires_at"),
    )


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


def _count_total_blogs(clinic_id: int) -> int:
    """베타 기간 전체(누적) 블로그 생성 횟수. DB 장애 시 -1 반환."""
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM usage_logs WHERE clinic_id = ? AND feature = 'blog_generation'",
                (clinic_id,),
            ).fetchone()
        return row["cnt"] if row else 0
    except Exception as exc:
        logger.warning("plan_guard: 블로그 총 횟수 조회 실패 (clinic_id=%s): %s", clinic_id, exc)
        return -1


def _count_total_prompt_copies(clinic_id: int) -> int:
    """베타 기간 전체(누적) 프롬프트 복사 횟수. DB 장애 시 -1 반환."""
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM usage_logs WHERE clinic_id = ? AND feature = 'prompt_copy'",
                (clinic_id,),
            ).fetchone()
        return row["cnt"] if row else 0
    except Exception as exc:
        logger.warning("plan_guard: 프롬프트 복사 횟수 조회 실패 (clinic_id=%s): %s", clinic_id, exc)
        return -1


def _count_total_image_sessions(clinic_id: int) -> int:
    """베타 기간 누적 이미지 세션 생성 횟수. DB 장애 시 -1 반환.
    image_sessions.idx_image_sessions_clinic 인덱스 적중.
    """
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM image_sessions WHERE clinic_id = ?",
                (clinic_id,),
            ).fetchone()
        return row["cnt"] if row else 0
    except Exception as exc:
        logger.warning(
            "plan_guard: 이미지 세션 횟수 조회 실패 (clinic_id=%s): %s",
            clinic_id, exc,
        )
        return -1


def check_image_session_limit(clinic_id: int) -> None:
    """이미지 세션 신규 생성 전 한도 체크. HTTPException(429) 발생 시 차단.

    /api/image/generate-initial 시작 부분에서 호출.

    K-8 (2026-05-04): 어뷰저가 블로그 본문 호출 없이 generate-initial 만 반복
    호출해서 OpenAI 비용 폭주시키는 진입점 차단. 유료 플랜(standard/pro)은
    무제한, free/trial 은 누적 30 (= blog_limit 25 + 5 buffer cancel/retry 흡수).
    """
    plan_data = _fetch_plan_data(clinic_id)
    if plan_data is None:
        # DB 장애 + 캐시 miss → fail open (유료 유저 차단 방지 우선)
        logger.warning(
            "plan_guard.check_image_session_limit: 플랜 정보 없음, fail open "
            "(clinic_id=%s)", clinic_id,
        )
        return

    effective = resolve_effective_plan(
        plan_data.get("plan_id"),
        plan_data.get("plan_expires_at"),
        plan_data.get("trial_expires_at"),
    )

    # 유료 플랜(standard/pro)만 무제한. trial 은 베타 한도 적용 (1차 베타 정책).
    if effective["has_unlimited"] and effective["plan_id"] != "trial":
        return

    count = _count_total_image_sessions(clinic_id)
    if count < 0:
        return  # DB 장애 → fail open

    if count >= _IMAGE_SESSION_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "image_session_limit_exceeded",
                "message": (
                    f"베타 누적 이미지 세션 생성 한도({_IMAGE_SESSION_LIMIT}회)에 도달했습니다. "
                    "Cligent 운영팀(cligent.ai@gmail.com)으로 문의해 주세요."
                ),
                "current": count,
                "limit": _IMAGE_SESSION_LIMIT,
            },
        )


def check_prompt_copy_limit(clinic_id: int) -> None:
    """
    프롬프트 복사 전 한도 체크. HTTPException(429) 발생 시 차단.
    main.py /api/blog/track-prompt-copy 에서 호출.
    """
    count = _count_total_prompt_copies(clinic_id)
    if count < 0:
        return  # DB 장애 → fail open
    if count >= _PROMPT_COPY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "prompt_copy_limit_exceeded",
                "message": f"베타 기간 프롬프트 복사 한도({_PROMPT_COPY_LIMIT}건)에 도달했습니다.",
                "current": count,
                "limit": _PROMPT_COPY_LIMIT,
            },
        )


def check_blog_limit(clinic_id: int) -> None:
    """
    블로그 생성 전 한도 체크. HTTPException(429) 발생 시 생성 차단.
    main.py /generate 엔드포인트에서 직접 호출.

    사용 예:
        check_blog_limit(user["clinic_id"])
    """
    plan_data = _fetch_plan_data(clinic_id)

    if plan_data is None:
        # DB 장애 + 캐시 miss → fail open (유료 유저 차단 방지 우선)
        logger.warning("plan_guard: 플랜 정보 없음, fail open (clinic_id=%s)", clinic_id)
        return

    effective = resolve_effective_plan(
        plan_data.get("plan_id"),
        plan_data.get("plan_expires_at"),
        plan_data.get("trial_expires_at"),
    )

    # 유료 플랜(standard/pro)만 진짜 무제한. trial은 베타 한도 적용 (1차 베타 정책).
    # 정식 출시 후 BYOAI 도입 시 trial 분기를 다시 has_unlimited에 포함시키면 됨.
    if effective["has_unlimited"] and effective["plan_id"] != "trial":
        return  # 유료 플랜 — 한도 없음

    # trial 또는 free → 베타 누적 한도 체크
    count = _count_total_blogs(clinic_id)
    if count < 0:
        return  # 사용량 조회 실패 → fail open

    if count >= _FREE_BLOG_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "plan_limit_exceeded",
                "message": (
                    f"베타 누적 블로그 생성 한도({_FREE_BLOG_LIMIT}편)에 도달했습니다. "
                    "Cligent 운영팀(cligent.ai@gmail.com)으로 문의해 주세요."
                ),
                "current": count,
                "limit": _FREE_BLOG_LIMIT,
            },
        )
