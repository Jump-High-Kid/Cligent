"""scripts/fetch_anatomy.py 단위 테스트 — 네트워크 mock."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_anatomy as fetch  # noqa: E402
import init_anatomy_part as init  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    anatomy = tmp_path / "data" / "anatomy"
    anatomy.mkdir(parents=True)
    shutil.copy(REPO / "data" / "anatomy" / "_SLUGS.json", anatomy / "_SLUGS.json")
    # init과 fetch 모두 같은 repo_root() 호출
    monkeypatch.setattr(init, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(fetch, "repo_root", lambda: tmp_path)
    return tmp_path


SERVIER_SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Anterior neck — Servier Medical Art</title>
  <meta property="og:image" content="https://smart.servier.com/wp-content/uploads/neck.svg" />
  <meta property="og:title" content="Anterior neck" />
</head>
<body>
  <p>Licensed under CC BY 4.0 — creativecommons.org/licenses/by/4.0/</p>
</body>
</html>
"""


WIKIMEDIA_SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>File:Skeleton.svg - Wikimedia Commons</title></head>
<body>This work is licensed under CC BY-SA 4.0</body>
</html>
"""


def test_detect_source_servier() -> None:
    assert (
        fetch.detect_source("https://smart.servier.com/smart_image/neck-3/")
        == "Servier Medical Art"
    )


def test_detect_source_anatomytool() -> None:
    assert fetch.detect_source("https://anatomytool.org/foo") == "AnatomyTOOL"


def test_detect_source_wikimedia() -> None:
    assert (
        fetch.detect_source("https://commons.wikimedia.org/wiki/File:Skeleton.svg")
        == "Wikimedia Commons"
    )


def test_detect_source_other() -> None:
    assert fetch.detect_source("https://example.com/anatomy") == "Other"


def test_parse_servier_page_extracts_title() -> None:
    info = fetch.parse_servier_page(SERVIER_SAMPLE_HTML)
    assert "Anterior neck" in info["title"]


def test_parse_servier_page_extracts_image_url() -> None:
    info = fetch.parse_servier_page(SERVIER_SAMPLE_HTML)
    assert info["image_url"] == "https://smart.servier.com/wp-content/uploads/neck.svg"


def test_parse_servier_page_detects_cc_by_4() -> None:
    info = fetch.parse_servier_page(SERVIER_SAMPLE_HTML)
    assert info["license"] == "CC BY 4.0"


def test_parse_servier_page_detects_cc_by_sa_4() -> None:
    info = fetch.parse_servier_page(WIKIMEDIA_SAMPLE_HTML)
    assert info["license"] == "CC BY-SA 4.0"


def test_determine_extension_from_url_svg() -> None:
    assert fetch.determine_extension("https://x/foo.svg", None) == "svg"


def test_determine_extension_from_url_png() -> None:
    assert fetch.determine_extension("https://x/foo.png?v=1", None) == "png"


def test_determine_extension_from_bytes_png() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    assert fetch.determine_extension(None, png_bytes) == "png"


def test_determine_extension_from_bytes_svg() -> None:
    svg_bytes = b'<?xml version="1.0"?><svg></svg>'
    assert fetch.determine_extension(None, svg_bytes) == "svg"


def test_fetch_and_save_manual_mode_creates_meta(fake_root: Path) -> None:
    """--manual 모드: Playwright 없이 동작."""
    result = fetch.fetch_and_save(
        slug="neck_anterior",
        url="https://smart.servier.com/test",
        view="anterior",
        manual=True,
    )
    assert result["manual_mode"] is True
    assert result["asset_id"] == "anatomy_neck_anterior_anterior_v1"
    target = fake_root / "data" / "anatomy" / "neck_anterior" / "meta.json"
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["source"] == "Servier Medical Art"
    assert loaded["source_url"] == "https://smart.servier.com/test"


def test_fetch_and_save_with_mocked_playwright(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fetch_page를 모킹해서 fetch 흐름 검증."""
    fake_svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'
    monkeypatch.setattr(
        fetch,
        "fetch_page",
        lambda url, timeout_ms=30000: (
            SERVIER_SAMPLE_HTML,
            fake_svg,
            "https://smart.servier.com/wp-content/uploads/neck.svg",
        ),
    )
    result = fetch.fetch_and_save(
        slug="neck_anterior",
        url="https://smart.servier.com/smart_image/neck-3/",
        view="anterior",
    )
    assert result["manual_mode"] is False
    assert result["image_saved"] == "data/anatomy/neck_anterior/source.svg"

    image_target = fake_root / "data" / "anatomy" / "neck_anterior" / "source.svg"
    assert image_target.exists()
    assert image_target.read_bytes() == fake_svg

    meta = json.loads(
        (fake_root / "data" / "anatomy" / "neck_anterior" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert "Anterior neck" in meta.get("notes", "")


def test_fetch_and_save_playwright_missing_falls_back_to_manual(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_import(*args: object, **kwargs: object) -> tuple:
        raise ImportError("playwright not installed")

    monkeypatch.setattr(fetch, "fetch_page", raise_import)
    result = fetch.fetch_and_save(
        slug="shoulder",
        url="https://smart.servier.com/test",
        view="anterior",
    )
    assert result["manual_mode"] is True


def test_fetch_and_save_unknown_slug_raises(fake_root: Path) -> None:
    with pytest.raises(ValueError, match="unknown slug"):
        fetch.fetch_and_save(
            slug="not_a_part",
            url="https://smart.servier.com/test",
            view="anterior",
            manual=True,
        )


def test_main_cli_manual_mode(
    fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = fetch.main(
        [
            "lumbar",
            "--url",
            "https://smart.servier.com/test",
            "--view",
            "posterior",
            "--manual",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "생성 완료" in captured.out
    assert "anatomy_lumbar_posterior_v1" in captured.out


def test_main_cli_url_required(fake_root: Path) -> None:
    with pytest.raises(SystemExit):
        fetch.main(["neck_anterior"])
