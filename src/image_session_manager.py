"""
image_session_manager.py — 이미지 세션 DB 헬퍼 (Phase 4, 2026-04-30)

세션 단위:
  1편 블로그당 1세션. initial 5장 생성 시 만들어지고 regen·edit 누적.
  세션 ID는 UUID4. 클라이언트는 initial 응답으로 받은 session_id를 이후 호출에 동봉.

DB 모델:
  image_sessions(session_id, clinic_id, user_id, blog_keyword, plan_id_at_start,
                 regen_count, edit_count, created_at, last_active_at)

함수:
  create_session(...) -> session_id (str)
  get_session(session_id) -> dict | None
  increment_regen(session_id, clinic_id) -> int (새 카운트)
  increment_edit(session_id, clinic_id) -> int
  list_recent_sessions(clinic_id, limit=20) -> list[dict]

장애 정책:
  - DB 에러는 RuntimeError로 raise (호출자가 503 변환)
  - clinic_id 불일치는 PermissionError (다른 한의원 세션 접근 차단)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(
    clinic_id: int,
    user_id: Optional[int],
    blog_keyword: str = "",
    plan_id_at_start: str = "",
) -> str:
    """새 이미지 세션 생성. session_id (UUID4) 반환."""
    from db_manager import get_db

    sid = str(uuid.uuid4())
    now = _now_iso()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO image_sessions
            (session_id, clinic_id, user_id, blog_keyword, plan_id_at_start,
             regen_count, edit_count, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
            """,
            (sid, clinic_id, user_id, blog_keyword, plan_id_at_start, now, now),
        )
    return sid


def get_session(session_id: str) -> Optional[dict]:
    """세션 정보 조회. 없으면 None."""
    from db_manager import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM image_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def _verify_owner(session_id: str, clinic_id: int) -> dict:
    """세션 존재 + clinic_id 일치 확인. 실패 시 예외."""
    sess = get_session(session_id)
    if sess is None:
        raise LookupError(f"이미지 세션을 찾을 수 없습니다: {session_id}")
    if sess["clinic_id"] != clinic_id:
        raise PermissionError("다른 한의원의 이미지 세션은 접근할 수 없습니다.")
    return sess


def increment_regen(session_id: str, clinic_id: int) -> int:
    """regen_count += 1. 새 카운트 반환."""
    from db_manager import get_db

    sess = _verify_owner(session_id, clinic_id)
    new_count = sess["regen_count"] + 1
    now = _now_iso()
    with get_db() as conn:
        conn.execute(
            "UPDATE image_sessions SET regen_count = ?, last_active_at = ? "
            "WHERE session_id = ?",
            (new_count, now, session_id),
        )
    return new_count


def increment_edit(session_id: str, clinic_id: int) -> int:
    """edit_count += 1. 새 카운트 반환."""
    from db_manager import get_db

    sess = _verify_owner(session_id, clinic_id)
    new_count = sess["edit_count"] + 1
    now = _now_iso()
    with get_db() as conn:
        conn.execute(
            "UPDATE image_sessions SET edit_count = ?, last_active_at = ? "
            "WHERE session_id = ?",
            (new_count, now, session_id),
        )
    return new_count


def list_recent_sessions(clinic_id: int, limit: int = 20) -> list[dict]:
    """클리닉의 최근 세션. 대시보드 KPI용."""
    from db_manager import get_db

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT session_id, blog_keyword, plan_id_at_start,
                   regen_count, edit_count, created_at, last_active_at
            FROM image_sessions
            WHERE clinic_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (clinic_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_clinic_image_stats(clinic_id: int) -> dict:
    """클리닉별 이미지 활동 집계. KPI 측정용.

    반환:
      total_sessions / total_regens / total_edits / avg_regen_per_session
    """
    from db_manager import get_db

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)            AS total_sessions,
                   COALESCE(SUM(regen_count), 0) AS total_regens,
                   COALESCE(SUM(edit_count), 0)  AS total_edits
            FROM image_sessions
            WHERE clinic_id = ?
            """,
            (clinic_id,),
        ).fetchone()

    total = row["total_sessions"] or 0
    regens = row["total_regens"] or 0
    edits = row["total_edits"] or 0
    return {
        "total_sessions": total,
        "total_regens": regens,
        "total_edits": edits,
        "avg_regen_per_session": (regens / total) if total > 0 else 0.0,
        "avg_edit_per_session": (edits / total) if total > 0 else 0.0,
    }


def get_user_image_stats(clinic_id: int, since: Optional[str] = None) -> dict:
    """사용자(클리닉) 대시보드용 이미지 카운트.

    1 세트 = initial 5장. regen·edit 호출 1회당 1장 추가 카운트.
    단순화: 총 이미지 수 ≈ sessions × 5 + regen_count + edit_count.

    Args:
        clinic_id: 본인 클리닉 ID.
        since: ISO datetime. 베타 가입일(clinics.created_at) 등 — 이후 세션만 집계.

    반환:
      sets_total / sets_this_month / images_total / images_this_month
    """
    from db_manager import get_db
    from datetime import datetime as _dt

    where = ["clinic_id = ?"]
    params: list = [clinic_id]
    if since:
        where.append("datetime(created_at) >= datetime(?)")
        params.append(since)

    sql_total = f"""
        SELECT COUNT(*) AS sets,
               COALESCE(SUM(5 + COALESCE(regen_count, 0) + COALESCE(edit_count, 0)), 0) AS images
        FROM image_sessions
        WHERE {' AND '.join(where)}
    """

    now = _dt.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    where_month = where + ["datetime(created_at) >= datetime(?)"]
    params_month = params + [month_start]
    sql_month = f"""
        SELECT COUNT(*) AS sets,
               COALESCE(SUM(5 + COALESCE(regen_count, 0) + COALESCE(edit_count, 0)), 0) AS images
        FROM image_sessions
        WHERE {' AND '.join(where_month)}
    """

    with get_db() as conn:
        row_total = conn.execute(sql_total, params).fetchone()
        row_month = conn.execute(sql_month, params_month).fetchone()

    return {
        "sets_total": int(row_total["sets"] or 0),
        "sets_this_month": int(row_month["sets"] or 0),
        "images_total": int(row_total["images"] or 0),
        "images_this_month": int(row_month["images"] or 0),
    }
