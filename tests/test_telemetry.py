"""
test_telemetry.py — Telemetry JSONL writer 회귀 (Commit 4)

배경 (2026-05-04):
  베타 KPI Commit 4 — 사용자 stuck 감지·이미지 취소 이벤트를 JSONL로 누적.
  KPI 어드민 페이지(Commit 6)가 stuck rate / cancel rate / 단계별 분포를 산출.

설계:
  - 순수 함수 record_event() — 호출자(엔드포인트)가 인증 후 clinic_id 만 넘김
  - fail-soft: I/O 실패 시 raise 없이 False 반환 (텔레메트리는 본 흐름 차단 금지)
  - JSONL append-only — data/agent_log.jsonl / feedback.jsonl 컨벤션 일치

검증 항목:
  1. record_event() 가 JSONL 1줄을 append
  2. 모든 필드 (ts / kind / clinic_id / session_id / stage / context) 직렬화
  3. 알 수 없는 kind → False 반환 (raise 없음)
  4. I/O 실패 → False (fail-soft)
  5. VALID_TELEMETRY_KINDS 상수 노출
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_valid_kinds_constant():
    """VALID_TELEMETRY_KINDS — stuck / cancel 최소 2종"""
    from telemetry import VALID_TELEMETRY_KINDS

    assert "stuck" in VALID_TELEMETRY_KINDS
    assert "cancel" in VALID_TELEMETRY_KINDS


def test_record_event_writes_jsonl_line(tmp_path):
    """기본 동작 — 1줄 append, 다음 호출은 2줄"""
    from telemetry import record_event

    log = tmp_path / "telemetry.jsonl"

    ok = record_event(
        kind="stuck",
        clinic_id=1,
        session_id="abc-123",
        stage="generating",
        context={"reason": "test"},
        log_path=log,
    )
    assert ok is True
    assert log.exists()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record_event(kind="cancel", clinic_id=1, log_path=log)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_record_event_required_fields(tmp_path):
    """JSONL 1줄 — ts / kind / clinic_id / session_id / stage / context 모두 포함"""
    from telemetry import record_event

    log = tmp_path / "telemetry.jsonl"
    record_event(
        kind="cancel",
        clinic_id=42,
        session_id="sess-uuid",
        stage="image",
        context={"image_session_id": "img-uuid", "elapsed_sec": 300},
        log_path=log,
    )
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert "ts" in row and isinstance(row["ts"], str) and len(row["ts"]) >= 19
    assert row["kind"] == "cancel"
    assert row["clinic_id"] == 42
    assert row["session_id"] == "sess-uuid"
    assert row["stage"] == "image"
    assert row["context"]["image_session_id"] == "img-uuid"
    assert row["context"]["elapsed_sec"] == 300


def test_record_event_invalid_kind_returns_false(tmp_path):
    """알 수 없는 kind → 파일에 쓰지 않고 False 반환 (raise 없음)"""
    from telemetry import record_event

    log = tmp_path / "telemetry.jsonl"
    ok = record_event(kind="bogus_kind", clinic_id=1, log_path=log)
    assert ok is False
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


def test_record_event_fail_soft_on_io_error(tmp_path):
    """I/O 실패 (디렉토리 미존재) → False 반환, raise 없음"""
    from telemetry import record_event

    bad_path = tmp_path / "nonexistent_dir" / "telemetry.jsonl"
    ok = record_event(kind="stuck", clinic_id=1, log_path=bad_path)
    assert ok is False  # 본 흐름 차단 금지


def test_record_event_optional_fields_default_to_none_or_empty(tmp_path):
    """session_id / stage / context 미제공 시 None 또는 {}"""
    from telemetry import record_event

    log = tmp_path / "telemetry.jsonl"
    record_event(kind="stuck", clinic_id=7, log_path=log)
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["clinic_id"] == 7
    # session_id / stage 는 null 가능, context 는 {} 권장
    assert row.get("session_id") in (None, "")
    assert row.get("stage") in (None, "")
    assert row.get("context") in (None, {})
