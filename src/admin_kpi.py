"""
admin_kpi.py — 베타 KPI 집계 SQL 모듈 (Commit 7b)

어드민 KPI 페이지(`/admin/kpi`)용 순수 SQL 집계 함수 4종. 라우터·인증·HTTP 의존성 0,
DB 만 읽는다. 모든 함수 fail-soft — DB 오류 시 빈 dict/list 리턴 (예외 raise 안 함).

사용처: routers/admin.py 의 KPI 페이지가 본 모듈을 호출해 dict/list 받아 템플릿 렌더.

함수
----
- get_cost_summary(days)         A1 비용 — kind별 SUM + 일별 trend
- get_module_satisfaction()      D1 모듈 만족도 — module별 like rate + count
- get_chat_turn_distribution()   A6 챗 길이 — turn_count 분포 + completion rate
- get_clinic_activity(days)      클리닉별 활동 — blog/image 세션 수 + 누적 비용
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from db_manager import get_db

logger = logging.getLogger(__name__)


# ── 시간 헬퍼 ──────────────────────────────────────────────


def _utc_cutoff_iso(days: int) -> str:
    """N일 전 UTC ISO 문자열. SQLite TEXT 컬럼과 lexicographic 비교 호환."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(days or 0), 0))
    return cutoff.isoformat()


# ── A1 비용 ───────────────────────────────────────────────


def get_cost_summary(days: int = 14) -> dict[str, Any]:
    """`cost_logs` 집계 — kind별 SUM(USD) + 일별 trend.

    반환:
        {
          "total_usd": float,
          "by_kind": {"anthropic_blog": 1.23, "openai_image_init": 0.42, ...},
          "by_day": [{"day": "2026-04-22", "usd": 0.12}, ...],   # ASC
          "days": int,
        }

    DB 오류 / 테이블 미존재 시 빈 구조 (total_usd=0, by_kind={}, by_day=[]).
    """
    cutoff = _utc_cutoff_iso(days)
    out: dict[str, Any] = {
        "total_usd": 0.0,
        "by_kind": {},
        "by_day": [],
        "days": int(days),
    }
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total "
                "FROM cost_logs WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()
            out["total_usd"] = float(row["total"] or 0.0)

            rows = conn.execute(
                "SELECT kind, COALESCE(SUM(cost_usd), 0) AS usd "
                "FROM cost_logs WHERE created_at >= ? "
                "GROUP BY kind",
                (cutoff,),
            ).fetchall()
            out["by_kind"] = {r["kind"]: float(r["usd"] or 0.0) for r in rows}

            rows = conn.execute(
                "SELECT substr(created_at, 1, 10) AS day, "
                "       COALESCE(SUM(cost_usd), 0) AS usd "
                "FROM cost_logs WHERE created_at >= ? "
                "GROUP BY day ORDER BY day ASC",
                (cutoff,),
            ).fetchall()
            out["by_day"] = [
                {"day": r["day"], "usd": float(r["usd"] or 0.0)} for r in rows
            ]
    except Exception:
        logger.exception("get_cost_summary failed (returning empty)")
    return out


# ── D1 모듈 만족도 ───────────────────────────────────────


def get_module_satisfaction() -> list[dict[str, Any]]:
    """`gallery_likes` 집계 — module별 like rate (liked=1/total) + total count.

    반환: 정렬된 list — module ASC, NULL(레거시) 마지막.
        [{"module": 1, "total": 12, "liked": 8, "rate": 0.6667}, ...,
         {"module": None, "total": 4, "liked": 1, "rate": 0.25}]

    rate = liked_count / total. total=0 인 module 은 결과에서 제외.
    """
    out: list[dict[str, Any]] = []
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT module, "
                "       COUNT(*) AS total, "
                "       COALESCE(SUM(liked), 0) AS liked "
                "FROM gallery_likes "
                "GROUP BY module "
                "ORDER BY (module IS NULL) ASC, module ASC"
            ).fetchall()
            for r in rows:
                total = int(r["total"] or 0)
                if total <= 0:
                    continue
                liked = int(r["liked"] or 0)
                out.append({
                    "module": r["module"],   # int 1~11 또는 None(레거시)
                    "total": total,
                    "liked": liked,
                    "rate": round(liked / total, 4) if total else 0.0,
                })
    except Exception:
        logger.exception("get_module_satisfaction failed (returning empty)")
    return out


# ── A6 챗 길이 ────────────────────────────────────────────


# turn_count 분포 bins. 각 bin = (lower_inclusive, upper_inclusive_or_none, label)
# upper=None 이면 lower 이상 모두.
_TURN_BINS: tuple[tuple[int, Optional[int], str], ...] = (
    (0, 0, "0"),
    (1, 2, "1-2"),
    (3, 4, "3-4"),
    (5, 7, "5-7"),
    (8, 12, "8-12"),
    (13, None, "13+"),
)


def get_chat_turn_distribution(days: int = 14) -> dict[str, Any]:
    """`blog_chat_sessions` 집계 — turn_count 히스토그램 + completion rate.

    반환:
        {
          "total": 25,
          "completion_rate": 0.6,        # stage='done' / total
          "avg_turns": 4.2,
          "histogram": [{"bin": "0", "count": 3}, ...],
          "days": int,
        }

    DB 오류 시 빈 구조.
    """
    cutoff = _utc_cutoff_iso(days)
    out: dict[str, Any] = {
        "total": 0,
        "completion_rate": 0.0,
        "avg_turns": 0.0,
        "histogram": [{"bin": label, "count": 0} for _, _, label in _TURN_BINS],
        "days": int(days),
    }
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                "       COALESCE(AVG(turn_count), 0) AS avg_turns, "
                "       SUM(CASE WHEN stage='done' THEN 1 ELSE 0 END) AS done_cnt "
                "FROM blog_chat_sessions "
                "WHERE last_active_at >= ?",
                (cutoff,),
            ).fetchone()
            total = int(row["total"] or 0)
            out["total"] = total
            out["avg_turns"] = round(float(row["avg_turns"] or 0.0), 2)
            done = int(row["done_cnt"] or 0)
            out["completion_rate"] = round(done / total, 4) if total else 0.0

            # 히스토그램 — bin 별 COUNT
            histogram: list[dict[str, Any]] = []
            for lower, upper, label in _TURN_BINS:
                if upper is None:
                    sql = (
                        "SELECT COUNT(*) AS c FROM blog_chat_sessions "
                        "WHERE last_active_at >= ? AND turn_count >= ?"
                    )
                    params: tuple = (cutoff, lower)
                else:
                    sql = (
                        "SELECT COUNT(*) AS c FROM blog_chat_sessions "
                        "WHERE last_active_at >= ? "
                        "AND turn_count BETWEEN ? AND ?"
                    )
                    params = (cutoff, lower, upper)
                r = conn.execute(sql, params).fetchone()
                histogram.append({"bin": label, "count": int(r["c"] or 0)})
            out["histogram"] = histogram
    except Exception:
        logger.exception("get_chat_turn_distribution failed (returning empty)")
    return out


# ── 클리닉별 활동 ────────────────────────────────────────


def get_clinic_activity(days: int = 14) -> list[dict[str, Any]]:
    """클리닉별 활동 — blog/image 세션 수 + 누적 비용 USD.

    반환: 비용 ↓ 정렬 (활성 클리닉 우선).
        [{"clinic_id": 1, "clinic_name": "강남한의원",
          "blog_sessions": 12, "image_sessions": 8, "cost_usd": 1.23}, ...]

    blog_sessions = blog_chat_sessions COUNT
    image_sessions = image_sessions COUNT
    cost_usd = cost_logs SUM (kind='openai_image_admin' 제외 — 어드민 테스트 분리)
    """
    cutoff = _utc_cutoff_iso(days)
    out: list[dict[str, Any]] = []
    try:
        with get_db() as conn:
            # 클리닉 전체 + 좌표 외부조인 — 활동 0인 클리닉도 포함하되,
            # 정렬상 비용 큰 순으로 잘라서 보여주기 위해 cost_usd ↓ 정렬.
            rows = conn.execute(
                """
                SELECT c.id              AS clinic_id,
                       c.name            AS clinic_name,
                       COALESCE(b.cnt, 0)  AS blog_sessions,
                       COALESCE(i.cnt, 0)  AS image_sessions,
                       COALESCE(co.usd, 0) AS cost_usd
                FROM clinics c
                LEFT JOIN (
                    SELECT clinic_id, COUNT(*) AS cnt
                    FROM blog_chat_sessions
                    WHERE last_active_at >= ?
                    GROUP BY clinic_id
                ) b ON b.clinic_id = c.id
                LEFT JOIN (
                    SELECT clinic_id, COUNT(*) AS cnt
                    FROM image_sessions
                    WHERE created_at >= ?
                    GROUP BY clinic_id
                ) i ON i.clinic_id = c.id
                LEFT JOIN (
                    SELECT clinic_id, SUM(cost_usd) AS usd
                    FROM cost_logs
                    WHERE created_at >= ?
                      AND kind != 'openai_image_admin'
                    GROUP BY clinic_id
                ) co ON co.clinic_id = c.id
                ORDER BY cost_usd DESC, blog_sessions DESC, c.id ASC
                """,
                (cutoff, cutoff, cutoff),
            ).fetchall()
            for r in rows:
                out.append({
                    "clinic_id": int(r["clinic_id"]),
                    "clinic_name": r["clinic_name"] or "",
                    "blog_sessions": int(r["blog_sessions"] or 0),
                    "image_sessions": int(r["image_sessions"] or 0),
                    "cost_usd": float(r["cost_usd"] or 0.0),
                })
    except Exception:
        logger.exception("get_clinic_activity failed (returning empty)")
    return out
