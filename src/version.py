"""
version.py — Cligent 버전 단일 진실원

루트 `VERSION` 파일을 읽어 `__version__`을 노출.
SemVer (MAJOR.MINOR.PATCH).

업데이트 규칙:
  - PATCH (0.9.0 → 0.9.1): 버그 수정
  - MINOR (0.9.0 → 0.10.0): 기능 추가, 베타 게이트 변경
  - MAJOR (0.x.y → 1.0.0): 정식 출시
"""
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _read_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


__version__: str = _read_version()
