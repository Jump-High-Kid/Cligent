"""
src/routers/billing.py — 플랜·사용량 조회 라우터

라우트 (2개):
  GET  /api/settings/plan/usage   플랜 정보 + 이번 달 사용량 (설정 > 시스템 & 보안)
  GET  /api/blog/beta-usage       베타 누적 사용량 (블로그/프롬프트 복사) + API 키 여부

M1+ 결제 라우트(구독 변경·청구·invoice)가 추가되면 같은 파일에 누적.

main.py 4,000줄 분할의 세 번째 라우터 (v0.9.0 / 2026-05-02).
auth.py · clinic.py 다음.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from auth_manager import get_current_user
from plan_guard import (
    _FREE_BLOG_LIMIT,
    _PROMPT_COPY_LIMIT,
    _count_total_blogs,
    _count_total_prompt_copies,
    resolve_effective_plan,
)

router = APIRouter()
_log = logging.getLogger(__name__)


@router.get("/api/settings/plan/usage")
async def get_plan_usage(user: dict = Depends(get_current_user)):
    """
    플랜 & 사용량 조회 — 설정 > 시스템 & 보안 > 플랜 & 사용량 탭용

    응답 예시:
    {
      "plan_id": "trial",
      "plan_name": "무료 체험",
      "trial_days_left": 7,
      "used_this_month": 2,
      "monthly_limit": 3,
      "usage_pct": 67
    }
    """
    clinic_id = user["clinic_id"]

    try:
        from db_manager import get_db
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")
        with get_db() as conn:
            clinic_row = conn.execute(
                """
                SELECT c.plan_id, c.plan_expires_at, c.trial_expires_at,
                       p.name AS plan_name, p.monthly_blog_limit
                FROM clinics c
                LEFT JOIN plans p ON c.plan_id = p.id
                WHERE c.id = ?
                """,
                (clinic_id,),
            ).fetchone()

            if not clinic_row:
                return JSONResponse({"detail": "클리닉 정보를 찾을 수 없습니다."}, status_code=404)

            usage_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM usage_logs
                WHERE clinic_id = ?
                  AND feature = 'blog_generation'
                  AND used_at >= ?
                """,
                (clinic_id, month_start),
            ).fetchone()
    except Exception as exc:
        _log.error("plan usage query failed (clinic_id=%s): %s", clinic_id, exc)
        return JSONResponse({"detail": "서버 오류"}, status_code=500)

    used = usage_row["cnt"] if usage_row else 0
    monthly_limit = clinic_row["monthly_blog_limit"]

    effective = resolve_effective_plan(
        clinic_row["plan_id"],
        clinic_row["plan_expires_at"],
        clinic_row["trial_expires_at"],
    )
    effective_plan = effective["plan_id"]

    plan_name_map = {
        "free": "무료",
        "trial": "무료 체험",
        "standard": "스탠다드",
        "pro": "프로",
    }
    plan_name = plan_name_map.get(effective_plan, clinic_row["plan_name"] or effective_plan)

    # 사용률: 무료 플랜일 때만 계산 (유료/체험은 무제한)
    if not effective["has_unlimited"] and monthly_limit and monthly_limit > 0:
        usage_pct = min(100, int(used / monthly_limit * 100))
    else:
        usage_pct = 0

    # 베타 누적 한도 (config.yaml beta.blog_limit_total 기반)
    # trial 만료 후 또는 free 플랜일 때 적용. trial 활성 상태에서도 참고용으로 노출.
    from plan_guard import _count_total_blogs
    beta_used = max(0, _count_total_blogs(clinic_id))
    beta_limit = _FREE_BLOG_LIMIT
    beta_pct = min(100, int(beta_used / beta_limit * 100)) if beta_limit > 0 else 0

    return JSONResponse({
        "plan_id": effective_plan,
        "plan_name": plan_name,
        "trial_days_left": effective["trial_days_left"],
        "used_this_month": used,
        "monthly_limit": monthly_limit,
        "usage_pct": usage_pct,
        "beta_used": beta_used,
        "beta_limit": beta_limit,
        "beta_pct": beta_pct,
    })


@router.get("/api/blog/beta-usage")
async def get_beta_usage(user: dict = Depends(get_current_user)):
    """베타 기간 사용량 조회 — 블로그 생성 / 프롬프트 복사 / API 키 여부"""
    clinic_id = user["clinic_id"]
    blog_count = max(0, _count_total_blogs(clinic_id))
    copy_count = max(0, _count_total_prompt_copies(clinic_id))

    api_key_configured = False
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT api_key_configured FROM clinics WHERE id = ?", (clinic_id,)
            ).fetchone()
        api_key_configured = bool(row["api_key_configured"]) if row else False
    except Exception:
        pass

    return JSONResponse({
        "blog_count": blog_count,
        "blog_limit": _FREE_BLOG_LIMIT,
        "copy_count": copy_count,
        "copy_limit": _PROMPT_COPY_LIMIT,
        "api_key_configured": api_key_configured,
    })
