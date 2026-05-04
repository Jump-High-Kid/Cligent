"""
gallery_likes.py — 갤러리 이미지 좋아요 (베타 KPI Commit 6, 2026-05-04)

D1 시그널: 모듈별 만족도 측정용. (session_id, image_index) UPSERT.
module 컬럼은 image_sessions.modules_json[index] 에서 denormalize —
KPI 집계 GROUP BY module 시 JSON 파싱 비용 회피.

함수:
  set_like(session_id, image_index, clinic_id, user_id, liked) -> dict
    UPSERT. liked=False 시 row 보존 (시간축 분석: 충동 클릭 vs 진짜 만족).
  get_session_likes(session_id, clinic_id) -> list[dict]
    image_index 0~4 순서. 없는 index 는 liked=False default.

장애 정책:
  - 잘못된 session_id → LookupError
  - 다른 clinic_id 접근 → PermissionError
  - image_index 범위 밖(0~4) → ValueError
  - DB 에러는 sqlite3 예외 그대로 전파 (호출자가 503 변환)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_VALID_IMAGE_INDEX_RANGE = range(5)  # 0~4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verify_session(session_id: str, clinic_id: int) -> dict:
    """image_session 존재 + clinic_id 일치 확인. 실패 시 예외."""
    from db_manager import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT clinic_id, modules_json FROM image_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        raise LookupError(f"이미지 세션을 찾을 수 없습니다: {session_id}")
    if row["clinic_id"] != clinic_id:
        raise PermissionError("다른 한의원의 이미지 세션은 접근할 수 없습니다.")
    return dict(row)


def _module_at_index(modules_json: Optional[str], image_index: int) -> Optional[int]:
    """modules_json 파싱 후 image_index 위치 module ID 반환. NULL/오류 시 None."""
    if not modules_json:
        return None
    try:
        modules = json.loads(modules_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(modules, list):
        return None
    if 0 <= image_index < len(modules):
        m = modules[image_index]
        return m if isinstance(m, int) else None
    return None


def set_like(
    session_id: str,
    image_index: int,
    clinic_id: int,
    user_id: Optional[int],
    liked: bool,
) -> dict:
    """좋아요 UPSERT. liked=False 시 row 유지(liked=0).

    반환:
      {session_id, image_index, liked, module, liked_at, updated_at}
    """
    if image_index not in _VALID_IMAGE_INDEX_RANGE:
        raise ValueError(f"image_index 는 0~4 범위여야 합니다: {image_index}")

    sess = _verify_session(session_id, clinic_id)
    module = _module_at_index(sess.get("modules_json"), image_index)
    now = _now_iso()
    liked_int = 1 if liked else 0

    from db_manager import get_db

    with get_db() as conn:
        # 기존 row 가 있는지 먼저 확인 — liked_at 보존 위해 (최초 좋아요 시각)
        existing = conn.execute(
            "SELECT liked_at FROM gallery_likes "
            "WHERE session_id = ? AND image_index = ?",
            (session_id, image_index),
        ).fetchone()
        liked_at = existing["liked_at"] if existing else now

        conn.execute(
            """
            INSERT INTO gallery_likes
                (session_id, image_index, clinic_id, user_id, module,
                 liked, liked_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, image_index) DO UPDATE SET
                liked = excluded.liked,
                module = excluded.module,
                user_id = excluded.user_id,
                updated_at = excluded.updated_at
            """,
            (session_id, image_index, clinic_id, user_id, module,
             liked_int, liked_at, now),
        )

    return {
        "session_id": session_id,
        "image_index": image_index,
        "liked": liked,
        "module": module,
        "liked_at": liked_at,
        "updated_at": now,
    }


def get_session_likes(session_id: str, clinic_id: int) -> list:
    """5장 좋아요 상태 반환. image_index 0~4 정렬.

    DB 에 row 없으면 liked=False default. module 은 modules_json 매핑.
    """
    sess = _verify_session(session_id, clinic_id)
    modules_json = sess.get("modules_json")

    from db_manager import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT image_index, liked, module, liked_at, updated_at "
            "FROM gallery_likes WHERE session_id = ? "
            "ORDER BY image_index ASC",
            (session_id,),
        ).fetchall()

    by_index = {r["image_index"]: r for r in rows}
    result: list = []
    for i in _VALID_IMAGE_INDEX_RANGE:
        existing = by_index.get(i)
        if existing is not None:
            result.append({
                "image_index": i,
                "liked": bool(existing["liked"]),
                "module": existing["module"],
                "liked_at": existing["liked_at"],
                "updated_at": existing["updated_at"],
            })
        else:
            result.append({
                "image_index": i,
                "liked": False,
                "module": _module_at_index(modules_json, i),
                "liked_at": None,
                "updated_at": None,
            })
    return result
