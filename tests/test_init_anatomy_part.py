"""scripts/init_anatomy_part.py 단위 테스트."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import init_anatomy_part as init  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    anatomy = tmp_path / "data" / "anatomy"
    anatomy.mkdir(parents=True)
    shutil.copy(REPO / "data" / "anatomy" / "_SLUGS.json", anatomy / "_SLUGS.json")
    monkeypatch.setattr(init, "repo_root", lambda: tmp_path)
    return tmp_path


def test_build_meta_known_slug(fake_root: Path) -> None:
    meta = init.build_meta(
        slug="neck_anterior",
        view="anterior",
        source="Servier Medical Art",
        license_str="CC BY 4.0",
    )
    assert meta["asset_id"] == "anatomy_neck_anterior_anterior_v1"
    assert meta["body_part_ko"] == "전경부"
    assert meta["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert meta["attribution_text"] == "Servier Medical Art licensed under CC BY 4.0"
    assert meta["file_path"] == "data/anatomy/neck_anterior/source.svg"


def test_build_meta_unknown_slug_raises(fake_root: Path) -> None:
    with pytest.raises(ValueError, match="unknown slug"):
        init.build_meta(
            slug="not_a_part",
            view="anterior",
            source="Servier Medical Art",
            license_str="CC BY 4.0",
        )


def test_build_meta_invalid_view_raises(fake_root: Path) -> None:
    with pytest.raises(ValueError, match="unknown view_angle"):
        init.build_meta(
            slug="neck_anterior",
            view="isometric",
            source="Servier Medical Art",
            license_str="CC BY 4.0",
        )


def test_build_meta_invalid_license_raises(fake_root: Path) -> None:
    with pytest.raises(ValueError, match="license not in whitelist"):
        init.build_meta(
            slug="neck_anterior",
            view="anterior",
            source="Servier Medical Art",
            license_str="MIT",
        )


def test_write_meta_creates_file(fake_root: Path) -> None:
    meta = init.build_meta(
        slug="shoulder",
        view="lateral",
        source="Servier Medical Art",
        license_str="CC BY 4.0",
    )
    target = init.write_meta("shoulder", meta)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["body_part_slug"] == "shoulder"
    assert loaded["view_angle"] == "lateral"


def test_write_meta_existing_raises_without_force(fake_root: Path) -> None:
    meta = init.build_meta(
        slug="hand",
        view="anterior",
        source="Servier Medical Art",
        license_str="CC BY 4.0",
    )
    init.write_meta("hand", meta)
    with pytest.raises(FileExistsError):
        init.write_meta("hand", meta)


def test_write_meta_force_overwrites(fake_root: Path) -> None:
    meta1 = init.build_meta(
        slug="hand",
        view="anterior",
        source="Servier Medical Art",
        license_str="CC BY 4.0",
    )
    init.write_meta("hand", meta1)
    meta2 = init.build_meta(
        slug="hand",
        view="posterior",
        source="Servier Medical Art",
        license_str="CC BY 4.0",
    )
    target = init.write_meta("hand", meta2, overwrite=True)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["view_angle"] == "posterior"


def test_main_cli_success(fake_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = init.main(["lumbar", "--view", "posterior"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "생성 완료" in captured.out
    assert "anatomy_lumbar_posterior_v1" in captured.out


def test_main_cli_unknown_slug_returns_2(
    fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = init.main(["nonexistent_part"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown slug" in err


def test_main_cli_with_url(fake_root: Path) -> None:
    rc = init.main(
        ["chest", "--url", "https://smart.servier.com/example/", "--view", "anterior"]
    )
    assert rc == 0
    target = fake_root / "data" / "anatomy" / "chest" / "meta.json"
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["source_url"] == "https://smart.servier.com/example/"


def test_main_cli_wikimedia_default_license(fake_root: Path) -> None:
    """Wikimedia source는 CC BY-SA 4.0 default."""
    rc = init.main(["skeleton", "--source", "Wikimedia Commons"])
    assert rc == 0
    target = fake_root / "data" / "anatomy" / "skeleton" / "meta.json"
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["license"] == "CC BY-SA 4.0"
