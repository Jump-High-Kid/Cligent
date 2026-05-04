"""
input_limits.py — 라우트 입력 길이/형식 검증 헬퍼

K-7 (베타 직전 보안 감사) 대응:
  무제한 입력으로 인한 LLM API 비용 폭주 차단 + 프롬프트 인젝션 부수 방어.

원칙:
  - 어뷰저에게 어느 필드인지 / 한도가 얼마인지 노출하지 않는다.
    → client 응답 메시지는 "입력 형식이 올바르지 않습니다." 일관.
  - 정상 사용자 디버깅 편의를 위해 server log 에는 field/한도 기록 (logger.info).
  - 빈 문자열은 통과 (필수 필드 검증은 호출 측 책임).
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# 모든 검증 실패에 동일 메시지 사용 (정보 비노출)
_GENERIC_400 = "입력 형식이 올바르지 않습니다."

# UUID4 정규식 (대소문자 무관)
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _fail(field: str, reason: str) -> None:
    """일관 400 + server log."""
    logger.info("input_limits 거부: field=%s reason=%s", field, reason)
    raise HTTPException(status_code=400, detail=_GENERIC_400)


def validate_str(value: Any, field: str, max_len: int) -> str:
    """문자열 길이 검증.

    - None/빈 문자열은 빈 문자열로 통과 (필수 여부는 호출 측에서 별도 검증).
    - 문자열이 아니면 거부.
    - 길이 초과 시 거부.
    반환: strip 된 문자열.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        _fail(field, f"type={type(value).__name__}")
    s = value.strip()
    if len(s) > max_len:
        _fail(field, f"len={len(s)} > {max_len}")
    return s


def validate_str_list(
    values: Any,
    field: str,
    max_items: int,
    max_each: int,
) -> list[str]:
    """문자열 리스트 검증 — 항목 개수 + 항목별 길이.

    - None 은 빈 리스트로 통과.
    - 리스트가 아니면 거부.
    - 항목 개수 초과 또는 항목 길이 초과 시 거부.
    반환: strip 된 비어있지 않은 항목 리스트.
    """
    if values is None:
        return []
    if not isinstance(values, list):
        _fail(field, f"type={type(values).__name__}")
    if len(values) > max_items:
        _fail(field, f"items={len(values)} > {max_items}")
    out: list[str] = []
    for i, v in enumerate(values):
        if not isinstance(v, str):
            _fail(field, f"item[{i}].type={type(v).__name__}")
        s = v.strip()
        if len(s) > max_each:
            _fail(field, f"item[{i}].len={len(s)} > {max_each}")
        if s:
            out.append(s)
    return out


def validate_int(
    value: Any,
    field: str,
    min_val: int,
    max_val: int,
    *,
    optional: bool = True,
) -> Optional[int]:
    """정수 범위 검증.

    - optional=True 이고 value 가 None 이면 None 반환.
    - 정수형 또는 정수로 변환 가능한 문자열 허용.
    - 범위 밖이면 거부.
    """
    if value is None:
        if optional:
            return None
        _fail(field, "missing")
    try:
        n = int(value)
    except (TypeError, ValueError):
        _fail(field, f"not int: {type(value).__name__}")
    if n < min_val or n > max_val:
        _fail(field, f"value={n} not in [{min_val},{max_val}]")
    return n


def validate_uuid(value: Any, field: str) -> str:
    """UUID4 형식 검증 (정규식).

    - 빈 문자열/None 은 호출 측에서 분기 후 호출해야 함 (이 함수는 비어있지 않다고 가정).
    - 실패 시 거부.
    반환: 소문자 정규화된 uuid 문자열.
    """
    if not isinstance(value, str) or not _UUID4_RE.match(value):
        _fail(field, "not uuid4")
    # 정규화 — DB 비교 시 일관성
    try:
        u = uuid.UUID(value)
        if u.version != 4:
            _fail(field, f"version={u.version}")
        return str(u)
    except (ValueError, AttributeError):
        _fail(field, "uuid parse")
        return ""  # unreachable
