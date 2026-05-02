"""
test_version.py — Cligent 버전 시스템 검증

- src/version.py 가 루트 VERSION 파일을 읽는지
- /api/version 엔드포인트가 SemVer 문자열을 반환하는지
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from version import __version__


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[\w.]+)?$")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_version_module_reads_version_file() -> None:
    """src/version.py 가 루트 VERSION 의 내용을 그대로 노출."""
    root = Path(__file__).resolve().parent.parent
    raw = (root / "VERSION").read_text(encoding="utf-8").strip()
    assert __version__ == raw
    assert SEMVER_RE.match(__version__), f"VERSION 형식 위반: {__version__!r}"


def test_api_version_endpoint(client: TestClient) -> None:
    """/api/version 은 인증 없이 200 + {version: ...} 반환."""
    res = client.get("/api/version")
    assert res.status_code == 200
    body = res.json()
    assert "version" in body
    assert body["version"] == __version__
    assert SEMVER_RE.match(body["version"])
