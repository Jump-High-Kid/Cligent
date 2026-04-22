"""
test_usage_tracker.py — usage_tracker.py 단위 테스트

시나리오:
  1. INSERT 성공 → usage_logs에 행 존재
  2. INSERT 실패 → 예외 없이 통과 (서비스 영향 없음)
"""

import sqlite3
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from usage_tracker import log_usage


class TestLogUsageSuccess:
    def test_inserts_row_on_success(self):
        """정상 케이스: usage_logs에 행이 삽입되어야 한다."""
        inserted = []

        mock_conn = MagicMock()
        mock_conn.execute = lambda sql, params: inserted.append(params)
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_get_db():
            yield mock_conn

        with patch("db_manager.get_db", mock_get_db):
            log_usage(clinic_id=1, feature="blog_generation", metadata={"keyword": "허리 통증"})

        assert len(inserted) == 1
        clinic_id, feature, used_at, meta_json = inserted[0]
        assert clinic_id == 1
        assert feature == "blog_generation"
        assert "허리 통증" in meta_json

    def test_none_metadata_stored_as_none(self):
        """metadata=None 이면 DB에 None 저장."""
        inserted = []

        mock_conn = MagicMock()
        mock_conn.execute = lambda sql, params: inserted.append(params)
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_get_db():
            yield mock_conn

        with patch("db_manager.get_db", mock_get_db):
            log_usage(clinic_id=2, feature="agent_chat")

        assert inserted[0][3] is None  # metadata 컬럼


class TestLogUsageFailure:
    def test_db_failure_does_not_raise(self):
        """DB INSERT 실패 → 예외 없이 통과해야 한다."""
        @contextmanager
        def mock_get_db():
            raise Exception("DB 연결 실패")
            yield  # unreachable

        with patch("db_manager.get_db", mock_get_db):
            # 예외가 올라오면 안 된다
            log_usage(clinic_id=3, feature="blog_generation")
