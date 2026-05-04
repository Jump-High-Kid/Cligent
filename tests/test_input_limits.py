"""
test_input_limits.py — K-7 입력 길이 검증 헬퍼 단위 테스트

src/input_limits.py 의 validate_str / validate_str_list / validate_int /
validate_uuid 동작을 격리 검증.
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from input_limits import (
    validate_int,
    validate_str,
    validate_str_list,
    validate_uuid,
)


# ---------- validate_str ----------

class TestValidateStr:
    def test_none_returns_empty(self):
        assert validate_str(None, "f", 100) == ""

    def test_empty_returns_empty(self):
        assert validate_str("", "f", 100) == ""

    def test_strip_whitespace(self):
        assert validate_str("  abc  ", "f", 100) == "abc"

    def test_within_limit(self):
        assert validate_str("hello", "f", 10) == "hello"

    def test_exact_limit(self):
        s = "a" * 100
        assert validate_str(s, "f", 100) == s

    def test_over_limit_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_str("a" * 101, "f", 100)
        assert exc.value.status_code == 400

    def test_non_string_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_str(123, "f", 100)
        assert exc.value.status_code == 400


# ---------- validate_str_list ----------

class TestValidateStrList:
    def test_none_returns_empty(self):
        assert validate_str_list(None, "f", 5, 100) == []

    def test_empty_returns_empty(self):
        assert validate_str_list([], "f", 5, 100) == []

    def test_within_limits(self):
        assert validate_str_list(["a", "b"], "f", 5, 100) == ["a", "b"]

    def test_filters_empty_items(self):
        assert validate_str_list(["a", "", "  ", "b"], "f", 5, 100) == ["a", "b"]

    def test_strips_items(self):
        assert validate_str_list(["  a  "], "f", 5, 100) == ["a"]

    def test_too_many_items_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_str_list(["a"] * 6, "f", 5, 100)
        assert exc.value.status_code == 400

    def test_item_too_long_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_str_list(["a" * 101], "f", 5, 100)
        assert exc.value.status_code == 400

    def test_non_list_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_str_list("not a list", "f", 5, 100)
        assert exc.value.status_code == 400

    def test_non_string_item_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_str_list([1, 2, 3], "f", 5, 100)
        assert exc.value.status_code == 400


# ---------- validate_int ----------

class TestValidateInt:
    def test_none_returns_none(self):
        assert validate_int(None, "f", 0, 100) is None

    def test_int_within_range(self):
        assert validate_int(50, "f", 0, 100) == 50

    def test_string_int_coerced(self):
        assert validate_int("42", "f", 0, 100) == 42

    def test_below_min_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_int(-1, "f", 0, 100)
        assert exc.value.status_code == 400

    def test_above_max_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_int(101, "f", 0, 100)
        assert exc.value.status_code == 400

    def test_non_int_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_int("abc", "f", 0, 100)
        assert exc.value.status_code == 400

    def test_required_missing_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_int(None, "f", 0, 100, optional=False)
        assert exc.value.status_code == 400


# ---------- validate_uuid ----------

class TestValidateUuid:
    def test_valid_uuid4(self):
        u = "12345678-1234-4abc-8def-1234567890ab"
        assert validate_uuid(u, "f") == u

    def test_uppercase_normalized(self):
        u_upper = "12345678-1234-4ABC-8DEF-1234567890AB"
        assert validate_uuid(u_upper, "f") == u_upper.lower()

    def test_uuid_v1_rejects(self):
        # v1 (시간 기반) 거부
        with pytest.raises(HTTPException) as exc:
            validate_uuid("12345678-1234-1abc-8def-1234567890ab", "f")
        assert exc.value.status_code == 400

    def test_garbage_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_uuid("not-a-uuid", "f")
        assert exc.value.status_code == 400

    def test_empty_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_uuid("", "f")
        assert exc.value.status_code == 400

    def test_non_string_rejects(self):
        with pytest.raises(HTTPException) as exc:
            validate_uuid(12345, "f")
        assert exc.value.status_code == 400

    def test_long_garbage_rejects(self):
        # 어뷰저가 1MB 문자열 던지는 경우
        with pytest.raises(HTTPException) as exc:
            validate_uuid("a" * 100000, "f")
        assert exc.value.status_code == 400
