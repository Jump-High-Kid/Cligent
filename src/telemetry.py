"""
telemetry.py — KPI 텔레메트리 JSONL 누적기 (Commit 4, 2026-05-04)

설계:
  - 클라이언트 (chat_state.js) 가 stuck 감지·이미지 취소 시 fire-and-forget POST
  - POST /api/telemetry/event 가 인증 후 record_event() 호출
  - JSONL append-only — data/agent_log.jsonl / feedback.jsonl 컨벤션 일치
  - fail-soft: I/O 실패 시 raise 없이 False 반환 (텔레메트리는 본 흐름 차단 금지)

소비자 (Commit 6 KPI 어드민):
  - data/telemetry.jsonl 을 읽어 stuck rate / cancel rate / 단계별 분포 산출

확장:
  - 새 kind 추가 → VALID_TELEMETRY_KINDS 에 추가
  - 새 필드 추가 → record_event() 시그니처 확장 (기존 줄은 None 으로 누락)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 프로젝트 루트의 data/telemetry.jsonl — 라우트가 별도 경로 미지정 시 사용
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH: Path = ROOT / "data" / "telemetry.jsonl"

# 인정되는 이벤트 종류
VALID_TELEMETRY_KINDS: tuple[str, ...] = ("stuck", "cancel")


def record_event(
    kind: str,
    clinic_id: int,
    session_id: Optional[str] = None,
    stage: Optional[str] = None,
    context: Optional[dict] = None,
    log_path: Optional[Path] = None,
) -> bool:
    """텔레메트리 이벤트 1건을 JSONL 에 append.

    Args:
        kind: VALID_TELEMETRY_KINDS 의 값 ("stuck" / "cancel" 등).
        clinic_id: 인증된 user.clinic_id (호출자가 책임지고 검증된 값을 전달).
        session_id: 블로그 챗 세션 ID (옵션).
        stage: 'generating' / 'image' / 'feedback' 등 (옵션).
        context: 추가 메타 (image_session_id / elapsed_sec 등). 직렬화 가능해야 함.
        log_path: 테스트용 override. 미지정 시 DEFAULT_LOG_PATH.

    Returns:
        True — 정상 기록.
        False — 알 수 없는 kind 또는 I/O 실패 (raise 없음, fail-soft).
    """
    if kind not in VALID_TELEMETRY_KINDS:
        logger.warning("telemetry: unknown kind=%s clinic_id=%s", kind, clinic_id)
        return False

    target = log_path if log_path is not None else DEFAULT_LOG_PATH

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "clinic_id": clinic_id,
        "session_id": session_id,
        "stage": stage,
        "context": context or {},
    }

    try:
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        logger.warning("telemetry: write failed path=%s err=%s", target, e)
        return False
