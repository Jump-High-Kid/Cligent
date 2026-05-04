"""
admin_kpi_cost.py — 어드민 비용·수익 KPI 집계 모듈 (Commit 8a, 2026-05-04)

`/admin/kpi` 비용 분석 패널 전용. baseline 분석 문서
`docs/cost_revenue_analysis_2026-05-04.md` 7장 SQL 3종을 그대로 이식 + 마진율·
정책 알림·연 사업소득 추정 함수 추가.

설계 원칙:
  - 라우터·HTTP 의존성 0. fail-soft (DB 오류 시 빈 dict/list 리턴, 예외 raise 안 함).
  - USD 단위 SUM 만 SQL 에서 처리. KRW 환산은 옵션 인자로 받음 (라우터에서 1회 조회).
  - plan 분류는 `plan_guard.resolve_effective_plan` 단일 진실원 재사용.

함수 (9 + record 1 = 10):
  - get_cost_per_blog(days)              A1 편당 평균 변동비 (kind별 + 합계)
  - get_margin_summary(days, rate)       A2 Standard/Pro 매출 vs 변동비 마진율
  - get_plan_distribution()              B1 플랜별 클리닉 분포
  - get_avg_usage_per_user(days)         B2 1인 월 평균 블로그/이미지 사용량
  - get_image_calls_per_blog(days)       B3 블로그 1편당 init/regen/edit 평균
  - get_pro_loss_risk_clinics(days, top) B4 변동비 > 매출 클리닉
  - get_billing_recon(months_back)       A3 OpenAI 청구서 vs 로깅 차이
  - record_billing_recon(ym, usd)        A3 월 청구서 입력 (UPSERT)
  - estimate_annual_revenue_30users(days) C1 30인 시 연 사업소득 추정
  - get_policy_alerts(days, rate)        C2 Pro edit/마진 임계값 초과 권장 알림
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from db_manager import get_db
from plan_guard import resolve_effective_plan

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = ROOT / "config.yaml"

# kind 분류 — cost_logger.VALID_COST_KINDS 와 동기 유지.
# 'openai_image_admin' 은 어드민 테스트 비용이라 KPI 합계에서 제외.
_TEXT_KINDS = ("anthropic_blog", "anthropic_meta")
_IMAGE_INIT = "openai_image_init"
_IMAGE_REGEN = "openai_image_regen"
_IMAGE_EDIT = "openai_image_edit"
_KPI_KINDS = _TEXT_KINDS + (_IMAGE_INIT, _IMAGE_REGEN, _IMAGE_EDIT)

# Standard / Pro 풀 사용 가정 (CLAUDE.md 가격 구조)
_STANDARD_BLOGS_PER_MONTH = 30
_PRO_BLOGS_PER_MONTH = 80


def _load_pricing_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("pricing", {}) or {}
    except Exception as exc:
        logger.warning("admin_kpi_cost: config.yaml 로드 실패 (%s)", exc)
        return {}


def _utc_cutoff_iso(days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(days or 0), 0))
    return cutoff.isoformat()


def _safe_div(num: float, denom: float) -> float:
    if not denom:
        return 0.0
    try:
        return num / denom
    except (TypeError, ZeroDivisionError):
        return 0.0


# ── A1 편당 평균 변동비 ───────────────────────────────────


def get_cost_per_blog(days: int = 30) -> dict[str, Any]:
    """편당 평균 변동비 (kind별 + 합계). USD 만 리턴 — KRW 환산은 호출자.

    blog_count 분모: cost_logs 의 DISTINCT blog_session_id (NULL 제외).
    blog_session_id 가 없는 row(어드민 테스트 등) 는 분자/분모 모두 제외.

    Returns:
        {
          "blog_count": int,
          "avg_usd": {
            "text": float,         # anthropic_blog + anthropic_meta
            "image_init": float,
            "image_regen": float,
            "image_edit": float,
            "total": float,        # 위 4종 합
          },
          "days": int,
        }
    """
    cutoff = _utc_cutoff_iso(days)
    out: dict[str, Any] = {
        "blog_count": 0,
        "avg_usd": {
            "text": 0.0, "image_init": 0.0, "image_regen": 0.0,
            "image_edit": 0.0, "total": 0.0,
        },
        "days": int(days),
    }
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT blog_session_id) AS cnt "
                "FROM cost_logs "
                "WHERE created_at >= ? AND blog_session_id IS NOT NULL "
                f"AND kind IN ({','.join('?' * len(_KPI_KINDS))})",
                (cutoff, *_KPI_KINDS),
            ).fetchone()
            blog_count = int(row["cnt"] or 0)
            out["blog_count"] = blog_count
            if blog_count == 0:
                return out

            # kind별 SUM
            rows = conn.execute(
                "SELECT kind, COALESCE(SUM(cost_usd), 0) AS usd "
                "FROM cost_logs "
                "WHERE created_at >= ? AND blog_session_id IS NOT NULL "
                f"AND kind IN ({','.join('?' * len(_KPI_KINDS))}) "
                "GROUP BY kind",
                (cutoff, *_KPI_KINDS),
            ).fetchall()
            sums = {r["kind"]: float(r["usd"] or 0.0) for r in rows}

            text_sum = sum(sums.get(k, 0.0) for k in _TEXT_KINDS)
            init_sum = sums.get(_IMAGE_INIT, 0.0)
            regen_sum = sums.get(_IMAGE_REGEN, 0.0)
            edit_sum = sums.get(_IMAGE_EDIT, 0.0)

            out["avg_usd"] = {
                "text": round(_safe_div(text_sum, blog_count), 6),
                "image_init": round(_safe_div(init_sum, blog_count), 6),
                "image_regen": round(_safe_div(regen_sum, blog_count), 6),
                "image_edit": round(_safe_div(edit_sum, blog_count), 6),
                "total": round(
                    _safe_div(text_sum + init_sum + regen_sum + edit_sum,
                              blog_count),
                    6,
                ),
            }
    except Exception:
        logger.exception("get_cost_per_blog failed (returning empty)")
    return out


# ── A2 마진율 ─────────────────────────────────────────────


def _margin_status(plan: str, margin_pct: float) -> str:
    """배지 상태: 'ok' / 'warn' / 'critical'.

    Pro 만 임계값 적용. Standard 는 마진 충분해 status 항상 'ok' (배지 없음).
    """
    if plan != "pro":
        return "ok"
    cfg = _load_pricing_config()
    try:
        critical = float(cfg.get("pro_margin_critical_pct", 30))
        warn = float(cfg.get("pro_margin_warn_pct", 50))
    except (TypeError, ValueError):
        critical, warn = 30.0, 50.0
    if margin_pct <= critical:
        return "critical"
    if margin_pct <= warn:
        return "warn"
    return "ok"


def get_margin_summary(
    days: int = 30,
    usd_to_krw_rate: Optional[float] = None,
) -> dict[str, Any]:
    """Standard / Pro 매출 vs 변동비 마진율.

    변동비는 plan 무관 전체 평균(get_cost_per_blog) 적용 — 베타 5인 규모는
    plan별 분기 분모가 너무 작아 노이즈만 큼. 매출은 plan별 단가 분기.

    Args:
        usd_to_krw_rate: 환율(원/달러). None 이면 fallback(1400).

    Returns:
        {
          "standard": {"revenue_krw", "cost_krw", "margin_krw",
                       "margin_pct", "status"},
          "pro":      {...},
          "blog_count": int,
          "rate": float,
          "days": int,
        }
    """
    cfg = _load_pricing_config()
    try:
        rate = float(usd_to_krw_rate) if usd_to_krw_rate is not None \
            else float(cfg.get("usd_to_krw_fallback", 1400))
    except (TypeError, ValueError):
        rate = 1400.0
    try:
        std_price = float(cfg.get("blog_price_krw_standard", 4967))
        pro_price = float(cfg.get("blog_price_krw_pro", 3488))
    except (TypeError, ValueError):
        std_price, pro_price = 4967.0, 3488.0

    cost = get_cost_per_blog(days=days)
    cost_usd = float(cost.get("avg_usd", {}).get("total", 0.0))
    cost_krw = round(cost_usd * rate, 2)

    out: dict[str, Any] = {
        "blog_count": int(cost.get("blog_count", 0)),
        "rate": rate,
        "days": int(days),
    }
    for plan, price in (("standard", std_price), ("pro", pro_price)):
        margin_krw = round(price - cost_krw, 2)
        margin_pct = round(_safe_div(margin_krw, price) * 100, 2)
        out[plan] = {
            "revenue_krw": round(price, 2),
            "cost_krw": cost_krw,
            "margin_krw": margin_krw,
            "margin_pct": margin_pct,
            "status": _margin_status(plan, margin_pct),
        }
    return out


# ── B1 플랜별 클리닉 분포 ─────────────────────────────────


def get_plan_distribution() -> dict[str, Any]:
    """clinics 테이블 + plan_guard.resolve_effective_plan 로 plan 분류.

    trial 은 별도 카운트 (UI 표시용) — A2 마진 계산에서는 Standard 와 동일하게 처리.

    Returns:
        {
          "total": int,
          "standard": int,
          "pro": int,
          "trial": int,
          "free": int,
          "standard_pct": float,    # of (standard + pro), trial/free 제외
          "pro_pct": float,
        }
    """
    out = {"total": 0, "standard": 0, "pro": 0, "trial": 0, "free": 0,
           "standard_pct": 0.0, "pro_pct": 0.0}
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, plan_id, plan_expires_at, trial_expires_at "
                "FROM clinics"
            ).fetchall()
            for r in rows:
                eff = resolve_effective_plan(
                    r["plan_id"], r["plan_expires_at"], r["trial_expires_at"],
                )
                pid = eff.get("plan_id", "free")
                if pid == "trial":
                    out["trial"] += 1
                elif pid == "pro":
                    out["pro"] += 1
                elif pid in ("standard", "basic"):
                    out["standard"] += 1
                else:
                    out["free"] += 1
                out["total"] += 1

            paid_total = out["standard"] + out["pro"]
            if paid_total > 0:
                out["standard_pct"] = round(
                    out["standard"] / paid_total * 100, 1
                )
                out["pro_pct"] = round(out["pro"] / paid_total * 100, 1)
    except Exception:
        logger.exception("get_plan_distribution failed (returning empty)")
    return out


# ── B2 1인 월 평균 사용량 ─────────────────────────────────


def get_avg_usage_per_user(days: int = 30) -> dict[str, Any]:
    """1인당 월 평균 블로그·이미지 사용량 + 풀 사용 대비 사용률 %.

    분모: blog_chat_sessions·image_sessions 의 DISTINCT user_id (NULL 제외).
    user_id 가 NULL 인 레거시 row 는 분자/분모 모두 제외 (노이즈 차단).
    days 가 30 이 아니면 월 환산 (× 30/days).
    """
    cutoff = _utc_cutoff_iso(days)
    days_safe = max(1, int(days))
    out: dict[str, Any] = {
        "user_count": 0,
        "avg_blogs_per_user_month": 0.0,
        "avg_image_sessions_per_user_month": 0.0,
        "blog_usage_pct_standard": 0.0,
        "blog_usage_pct_pro": 0.0,
        "days": int(days),
    }
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS sessions, "
                "       COUNT(DISTINCT user_id) AS users "
                "FROM blog_chat_sessions "
                "WHERE last_active_at >= ? AND user_id IS NOT NULL "
                "AND stage = 'done'",
                (cutoff,),
            ).fetchone()
            blog_sessions = int(row["sessions"] or 0)
            user_count = int(row["users"] or 0)

            row2 = conn.execute(
                "SELECT COUNT(*) AS cnt "
                "FROM image_sessions WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()
            image_sessions = int(row2["cnt"] or 0)

            out["user_count"] = user_count
            if user_count > 0:
                blogs_per_window = blog_sessions / user_count
                imgs_per_window = image_sessions / user_count
                month_factor = 30.0 / days_safe
                blogs_per_month = round(blogs_per_window * month_factor, 2)
                imgs_per_month = round(imgs_per_window * month_factor, 2)
                out["avg_blogs_per_user_month"] = blogs_per_month
                out["avg_image_sessions_per_user_month"] = imgs_per_month
                out["blog_usage_pct_standard"] = round(
                    blogs_per_month / _STANDARD_BLOGS_PER_MONTH * 100, 1
                )
                out["blog_usage_pct_pro"] = round(
                    blogs_per_month / _PRO_BLOGS_PER_MONTH * 100, 1
                )
    except Exception:
        logger.exception("get_avg_usage_per_user failed (returning empty)")
    return out


# ── B3 블로그 1편당 이미지 호출 평균 ──────────────────────


def get_image_calls_per_blog(days: int = 30) -> dict[str, Any]:
    """블로그 1편당 init/regen/edit 평균 호출 횟수.

    분모: cost_logs 의 DISTINCT blog_session_id (이미지 kind 한정, NULL 제외).
    init 평균은 거의 1.0 (검증용), regen·edit 가 핵심 (Pro 적자 판정).
    """
    cutoff = _utc_cutoff_iso(days)
    out: dict[str, Any] = {
        "blog_count": 0,
        "init": 0.0, "regen": 0.0, "edit": 0.0,
        "days": int(days),
    }
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT blog_session_id) AS cnt "
                "FROM cost_logs "
                "WHERE created_at >= ? AND blog_session_id IS NOT NULL "
                "AND kind IN (?, ?, ?)",
                (cutoff, _IMAGE_INIT, _IMAGE_REGEN, _IMAGE_EDIT),
            ).fetchone()
            blog_count = int(row["cnt"] or 0)
            out["blog_count"] = blog_count
            if blog_count == 0:
                return out

            rows = conn.execute(
                "SELECT kind, COUNT(*) AS calls "
                "FROM cost_logs "
                "WHERE created_at >= ? AND blog_session_id IS NOT NULL "
                "AND kind IN (?, ?, ?) "
                "GROUP BY kind",
                (cutoff, _IMAGE_INIT, _IMAGE_REGEN, _IMAGE_EDIT),
            ).fetchall()
            counts = {r["kind"]: int(r["calls"] or 0) for r in rows}

            out["init"] = round(
                _safe_div(counts.get(_IMAGE_INIT, 0), blog_count), 2
            )
            out["regen"] = round(
                _safe_div(counts.get(_IMAGE_REGEN, 0), blog_count), 2
            )
            out["edit"] = round(
                _safe_div(counts.get(_IMAGE_EDIT, 0), blog_count), 2
            )
    except Exception:
        logger.exception("get_image_calls_per_blog failed (returning empty)")
    return out


# ── B4 Pro 적자 위험 클리닉 ──────────────────────────────


def get_pro_loss_risk_clinics(
    days: int = 30,
    top: int = 10,
    usd_to_krw_rate: Optional[float] = None,
) -> list[dict[str, Any]]:
    """클리닉별 누적 변동비 vs 매출 (변동비 > 매출 만).

    매출 추정: 클리닉의 effective plan 단가 × 완료 블로그 편 수.
    완료 편 수: blog_chat_sessions stage='done' COUNT.
    plan=trial/free 는 매출 0 처리 (베타1 무료) — 모두 적자로 표시되니
    standard/pro 만 필터링.
    """
    cfg = _load_pricing_config()
    try:
        rate = float(usd_to_krw_rate) if usd_to_krw_rate is not None \
            else float(cfg.get("usd_to_krw_fallback", 1400))
        std_price = float(cfg.get("blog_price_krw_standard", 4967))
        pro_price = float(cfg.get("blog_price_krw_pro", 3488))
    except (TypeError, ValueError):
        rate, std_price, pro_price = 1400.0, 4967.0, 3488.0
    cutoff = _utc_cutoff_iso(days)
    top_n = max(1, int(top))

    out: list[dict[str, Any]] = []
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.name, c.plan_id, c.plan_expires_at,
                       c.trial_expires_at,
                       COALESCE(b.cnt, 0) AS blogs,
                       COALESCE(co.usd, 0) AS cost_usd
                FROM clinics c
                LEFT JOIN (
                    SELECT clinic_id, COUNT(*) AS cnt
                    FROM blog_chat_sessions
                    WHERE last_active_at >= ? AND stage = 'done'
                    GROUP BY clinic_id
                ) b ON b.clinic_id = c.id
                LEFT JOIN (
                    SELECT clinic_id, SUM(cost_usd) AS usd
                    FROM cost_logs
                    WHERE created_at >= ?
                      AND kind != 'openai_image_admin'
                    GROUP BY clinic_id
                ) co ON co.clinic_id = c.id
                """,
                (cutoff, cutoff),
            ).fetchall()

            for r in rows:
                eff = resolve_effective_plan(
                    r["plan_id"], r["plan_expires_at"], r["trial_expires_at"],
                )
                pid = eff.get("plan_id", "free")
                if pid not in ("standard", "pro"):
                    continue  # 베타1 trial/free 는 매출 0 — 패널 노이즈 회피

                price = pro_price if pid == "pro" else std_price
                blogs = int(r["blogs"] or 0)
                revenue = round(blogs * price, 2)
                cost_krw = round(float(r["cost_usd"] or 0.0) * rate, 2)
                if cost_krw <= revenue:
                    continue
                out.append({
                    "clinic_id": int(r["id"]),
                    "clinic_name": r["name"] or "",
                    "plan": pid,
                    "blogs": blogs,
                    "revenue_krw": revenue,
                    "cost_krw": cost_krw,
                    "loss_krw": round(cost_krw - revenue, 2),
                })
            out.sort(key=lambda x: x["loss_krw"], reverse=True)
            out = out[:top_n]
    except Exception:
        logger.exception("get_pro_loss_risk_clinics failed (returning empty)")
    return out


# ── A3 OpenAI 청구서 vs 로깅 차이 ────────────────────────


def get_billing_recon(months_back: int = 3) -> list[dict[str, Any]]:
    """admin_billing_recon SELECT — 최근 N월 청구서 vs cost_logs 차이.

    차이 % = (invoice - logged) / invoice * 100
    invoice=0 또는 미입력 시 diff_pct=None (UI 에서 "미입력" 표시).
    """
    months = max(1, int(months_back))
    out: list[dict[str, Any]] = []
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT year_month, openai_invoice_usd, our_logged_usd, "
                "       recorded_at "
                "FROM admin_billing_recon "
                "ORDER BY year_month DESC LIMIT ?",
                (months,),
            ).fetchall()
            for r in rows:
                invoice = float(r["openai_invoice_usd"] or 0.0)
                logged = float(r["our_logged_usd"] or 0.0)
                diff_pct: Optional[float]
                if invoice > 0:
                    diff_pct = round((invoice - logged) / invoice * 100, 2)
                else:
                    diff_pct = None
                out.append({
                    "year_month": r["year_month"],
                    "openai_invoice_usd": round(invoice, 4),
                    "our_logged_usd": round(logged, 4),
                    "diff_usd": round(invoice - logged, 4),
                    "diff_pct": diff_pct,
                    "recorded_at": r["recorded_at"],
                })
    except Exception:
        logger.exception("get_billing_recon failed (returning empty)")
    return out


def record_billing_recon(year_month: str, openai_invoice_usd: float) -> bool:
    """월별 OpenAI 청구액 입력 (UPSERT). our_logged_usd 는 cost_logs 에서 자동 계산.

    Args:
        year_month: 'YYYY-MM' 형식. 형식 위반 시 False.
        openai_invoice_usd: USD 단위. 음수/NaN 시 False.

    Returns:
        True INSERT/UPDATE 성공, False 검증/DB 실패 (raise 없음).
    """
    # YYYY-MM 형식 검증
    try:
        datetime.strptime(year_month, "%Y-%m")
    except (TypeError, ValueError):
        return False
    try:
        invoice = float(openai_invoice_usd)
    except (TypeError, ValueError):
        return False
    if invoice < 0 or invoice != invoice:  # NaN check
        return False

    try:
        with get_db() as conn:
            # cost_logs 에서 해당 월의 our_logged_usd 자동 합산
            # (kind='openai_image_admin' 제외 — 어드민 테스트 분리)
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS usd "
                "FROM cost_logs "
                "WHERE substr(created_at, 1, 7) = ? "
                "AND kind != 'openai_image_admin' "
                "AND kind LIKE 'openai_%'",
                (year_month,),
            ).fetchone()
            logged = float(row["usd"] or 0.0)

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO admin_billing_recon
                    (year_month, openai_invoice_usd, our_logged_usd, recorded_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(year_month) DO UPDATE SET
                    openai_invoice_usd = excluded.openai_invoice_usd,
                    our_logged_usd     = excluded.our_logged_usd,
                    recorded_at        = excluded.recorded_at
                """,
                (year_month, invoice, logged, now_iso),
            )
        return True
    except Exception as exc:
        logger.warning("record_billing_recon failed (%s)", exc)
        return False


# ── C1 30인 시 연 사업소득 추정 ──────────────────────────


def estimate_annual_revenue_30users(
    days: int = 30,
    usd_to_krw_rate: Optional[float] = None,
) -> dict[str, Any]:
    """평균 1인당 월 마진 × 30인 × 12 = 연 사업소득 추정.

    월 마진 가중평균 — Standard 70 / Pro 30 비율 (B1 실측 있으면 그 비율 적용).

    Returns:
        {"monthly_margin_per_user_krw": float,
         "annual_revenue_30users_krw": float,
         "weights": {"standard": 0.7, "pro": 0.3},
         "days": int}
    """
    cfg = _load_pricing_config()
    try:
        rate = float(usd_to_krw_rate) if usd_to_krw_rate is not None \
            else float(cfg.get("usd_to_krw_fallback", 1400))
    except (TypeError, ValueError):
        rate = 1400.0

    margin = get_margin_summary(days=days, usd_to_krw_rate=rate)
    usage = get_avg_usage_per_user(days=days)
    dist = get_plan_distribution()

    # 가중치: B1 실측 있으면 적용, 없으면 70/30 기본
    paid_total = dist.get("standard", 0) + dist.get("pro", 0)
    if paid_total > 0:
        w_std = dist["standard"] / paid_total
        w_pro = dist["pro"] / paid_total
    else:
        w_std, w_pro = 0.7, 0.3

    blogs_per_month = float(usage.get("avg_blogs_per_user_month", 0.0))
    std_margin_per_blog = float(margin.get("standard", {}).get("margin_krw", 0.0))
    pro_margin_per_blog = float(margin.get("pro", {}).get("margin_krw", 0.0))

    monthly_margin = round(
        blogs_per_month * (w_std * std_margin_per_blog + w_pro * pro_margin_per_blog),
        2,
    )
    annual_30 = round(monthly_margin * 30 * 12, 2)

    return {
        "monthly_margin_per_user_krw": monthly_margin,
        "annual_revenue_30users_krw": annual_30,
        "blogs_per_month": blogs_per_month,
        "weights": {
            "standard": round(w_std, 4),
            "pro": round(w_pro, 4),
        },
        "days": int(days),
    }


# ── C2 정책 권장 알림 ────────────────────────────────────


def get_policy_alerts(
    days: int = 30,
    usd_to_krw_rate: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Pro edit 평균 / Pro 마진율 임계값 초과 시 정책 조정 권장.

    Returns:
        [{"severity": "warn"|"critical",
          "kind": "edit_high"|"pro_margin_low",
          "value": float,
          "threshold": float,
          "message": str}, ...]
        조건 미충족 시 빈 list.
    """
    cfg = _load_pricing_config()
    try:
        edit_warn = float(cfg.get("edit_avg_warn", 3.0))
        margin_warn = float(cfg.get("pro_margin_warn_pct", 50))
        margin_critical = float(cfg.get("pro_margin_critical_pct", 30))
    except (TypeError, ValueError):
        edit_warn, margin_warn, margin_critical = 3.0, 50.0, 30.0

    alerts: list[dict[str, Any]] = []

    # edit 평균
    img_calls = get_image_calls_per_blog(days=days)
    edit_avg = float(img_calls.get("edit", 0.0))
    if edit_avg >= edit_warn:
        alerts.append({
            "severity": "warn",
            "kind": "edit_high",
            "value": edit_avg,
            "threshold": edit_warn,
            "message": (
                f"Pro 평균 edit 호출 {edit_avg:.2f}회 ≥ {edit_warn:.1f} — "
                "edit 무료 한도 축소 또는 가격 인상 검토 권장"
            ),
        })

    # Pro 마진율
    margin = get_margin_summary(days=days, usd_to_krw_rate=usd_to_krw_rate)
    pro_pct = float(margin.get("pro", {}).get("margin_pct", 100.0))
    if pro_pct <= margin_critical:
        alerts.append({
            "severity": "critical",
            "kind": "pro_margin_low",
            "value": pro_pct,
            "threshold": margin_critical,
            "message": (
                f"Pro 마진율 {pro_pct:.1f}% ≤ {margin_critical:.0f}% — "
                "Pro 가격 정책 즉시 재검토 필요"
            ),
        })
    elif pro_pct <= margin_warn:
        alerts.append({
            "severity": "warn",
            "kind": "pro_margin_low",
            "value": pro_pct,
            "threshold": margin_warn,
            "message": (
                f"Pro 마진율 {pro_pct:.1f}% ≤ {margin_warn:.0f}% — "
                "적자 위험, 정책 재검토 검토 권장"
            ),
        })

    return alerts
