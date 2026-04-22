"""
usage_tracker.py — 기능별 사용량 로그 기록

규칙:
  - INSERT 실패 시 예외를 올리지 않고 경고 로그만 남김
  - 서비스 장애 원인이 되면 안 된다
  - agent_middleware.py에서 from usage_tracker import log_usage 로 호출
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def log_usage(
    clinic_id: int,
    feature: str,
    metadata: Optional[dict] = None,
) -> None:
    """
    usage_logs 테이블에 사용량 기록.

    Args:
        clinic_id: 한의원 ID
        feature:   기능 식별자 ('blog_generation', 'agent_chat', 등)
        metadata:  추가 정보 (토큰 수, 모델명 등). JSON 직렬화 가능해야 함.
    """
    try:
        from db_manager import get_db
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        with get_db() as conn:
            conn.execute(
                "INSERT INTO usage_logs (clinic_id, feature, used_at, metadata) VALUES (?, ?, ?, ?)",
                (clinic_id, feature, now, meta_json),
            )
    except Exception as exc:
        logger.warning(
            "usage_tracker: log_usage 실패 (clinic_id=%s, feature=%s): %s",
            clinic_id, feature, exc,
        )
