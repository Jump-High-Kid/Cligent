"""validate_anatomy_meta.py 단위 테스트 — 14 시나리오."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# scripts/ 디렉토리를 import 경로에 추가
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_anatomy_meta as v  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tmp_path를 가짜 repo root로 사용. _SLUGS.json/_schema.json 복사."""
    anatomy = tmp_path / "data" / "anatomy"
    anatomy.mkdir(parents=True)
    shutil.copy(REPO / "data" / "anatomy" / "_SLUGS.json", anatomy / "_SLUGS.json")
    shutil.copy(REPO / "data" / "anatomy" / "_schema.json", anatomy / "_schema.json")
    monkeypatch.setattr(v, "repo_root", lambda: tmp_path)
    return tmp_path


def _valid_meta() -> dict:
    return {
        "asset_id": "anatomy_neck_anterior_anterior_v1",
        "body_part_slug": "neck_anterior",
        "body_part_ko": "전경부",
        "view_angle": "anterior",
        "source": "Servier Medical Art",
        "source_url": "https://smart.servier.com/smart_image/neck-3/",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution_text": "Servier Medical Art licensed under CC BY 4.0",
        "downloaded_at": "2026-05-01",
        "file_path": "data/anatomy/neck_anterior/source.svg",
    }


def _write_meta(root: Path, slug: str, meta: dict, with_file: bool = True) -> Path:
    part_dir = root / "data" / "anatomy" / slug
    part_dir.mkdir(parents=True, exist_ok=True)
    if with_file:
        (part_dir / "source.svg").write_text(
            '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>',
            encoding="utf-8",
        )
    target = part_dir / "meta.json"
    target.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


# 1
def test_canonical_attribution() -> None:
    assert v.build_canonical_attribution("Servier Medical Art", "CC BY 4.0") == (
        "Servier Medical Art licensed under CC BY 4.0"
    )


# 2
def test_full_valid_meta_passes(fake_root: Path) -> None:
    _write_meta(fake_root, "neck_anterior", _valid_meta())
    assert v.run() == 0


# 3
def test_missing_required_field_fails(fake_root: Path) -> None:
    meta = _valid_meta()
    del meta["source_url"]
    _write_meta(fake_root, "neck_anterior", meta)
    assert v.run() == 1


# 4
def test_license_not_in_whitelist_fails(fake_root: Path) -> None:
    meta = _valid_meta()
    meta["license"] = "MIT"
    _write_meta(fake_root, "neck_anterior", meta)
    assert v.run() == 1


# 5
def test_file_path_not_found_fails(fake_root: Path) -> None:
    _write_meta(fake_root, "neck_anterior", _valid_meta(), with_file=False)
    assert v.run() == 1


# 6
def test_view_angle_invalid_fails(fake_root: Path) -> None:
    meta = _valid_meta()
    meta["view_angle"] = "isometric"
    _write_meta(fake_root, "neck_anterior", meta)
    assert v.run() == 1


# 7
def test_duplicate_asset_id_fails(fake_root: Path) -> None:
    m1 = _valid_meta()
    m2 = _valid_meta()
    m2["body_part_slug"] = "shoulder"
    m2["body_part_ko"] = "견관절"
    m2["file_path"] = "data/anatomy/shoulder/source.svg"
    # asset_id는 그대로 유지 → 중복
    _write_meta(fake_root, "neck_anterior", m1)
    _write_meta(fake_root, "shoulder", m2)
    assert v.run() == 1


# 8
def test_attribution_mismatch_fails(fake_root: Path) -> None:
    meta = _valid_meta()
    meta["attribution_text"] = "Wrong attribution string"
    _write_meta(fake_root, "neck_anterior", meta)
    assert v.run() == 1


# 9
def test_attribution_fix_repairs_and_passes(fake_root: Path) -> None:
    meta = _valid_meta()
    meta["attribution_text"] = "Wrong attribution string"
    meta_path = _write_meta(fake_root, "neck_anterior", meta)
    assert v.run(fix=True) == 0
    fixed = json.loads(meta_path.read_text(encoding="utf-8"))
    assert fixed["attribution_text"] == "Servier Medical Art licensed under CC BY 4.0"


# 10
def test_unknown_slug_fails(fake_root: Path) -> None:
    meta = _valid_meta()
    meta["body_part_slug"] = "unknown_part"
    _write_meta(fake_root, "neck_anterior", meta)
    assert v.run() == 1


# 11
def test_body_part_ko_mismatch_fails(fake_root: Path) -> None:
    meta = _valid_meta()
    meta["body_part_ko"] = "잘못된 한글"
    _write_meta(fake_root, "neck_anterior", meta)
    assert v.run() == 1


# 12
def test_progress_count(fake_root: Path) -> None:
    _write_meta(fake_root, "neck_anterior", _valid_meta())
    done, total, pending = v.compute_progress(v.load_slugs())
    assert total == 30
    assert done == 1
    assert "neck_anterior" not in pending
    assert "shoulder" in pending


# 13
def test_strict_fails_under_30(fake_root: Path) -> None:
    _write_meta(fake_root, "neck_anterior", _valid_meta())
    assert v.run(strict=True) == 1


# 14
def test_invalid_json_fails(fake_root: Path) -> None:
    part_dir = fake_root / "data" / "anatomy" / "neck_anterior"
    part_dir.mkdir(parents=True)
    (part_dir / "meta.json").write_text("{ broken json", encoding="utf-8")
    assert v.run() == 1


# bonus: empty state
def test_empty_state_passes_non_strict(fake_root: Path) -> None:
    assert v.run() == 0


def test_empty_state_fails_strict(fake_root: Path) -> None:
    assert v.run(strict=True) == 1


def test_extension_not_in_whitelist_fails(fake_root: Path) -> None:
    meta = _valid_meta()
    meta["file_path"] = "data/anatomy/neck_anterior/source.jpg"
    part_dir = fake_root / "data" / "anatomy" / "neck_anterior"
    part_dir.mkdir(parents=True, exist_ok=True)
    (part_dir / "source.jpg").write_text("fake", encoding="utf-8")
    (part_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert v.run() == 1
