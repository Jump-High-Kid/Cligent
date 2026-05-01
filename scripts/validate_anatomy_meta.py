#!/usr/bin/env python3
"""
해부학 DB Phase 1 — meta.json 검증 + --fix + 진행률.

체크 항목:
    - JSON Schema (필수 필드, enum, format) — _schema.json 기반
    - file_path 실제 파일 존재 + 확장자(svg/png) 화이트리스트
    - asset_id 패턴 + 중복
    - body_part_slug가 _SLUGS.json에 등록됐는지
    - body_part_ko가 _SLUGS.json과 일치
    - attribution_text가 canonical (source + license)과 일치 — --fix 자동 수정
    - 30 부위 진행률

Exit codes:
    0 = 검증 통과 (부분 완료도 OK, --strict 시만 100% 강제)
    1 = 검증 실패 (스키마/파일 위반)
    2 = 사용 에러
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def anatomy_dir() -> Path:
    return repo_root() / "data" / "anatomy"


def load_schema() -> dict:
    with (anatomy_dir() / "_schema.json").open(encoding="utf-8") as f:
        return json.load(f)


def load_slugs() -> dict:
    with (anatomy_dir() / "_SLUGS.json").open(encoding="utf-8") as f:
        return json.load(f)


def build_canonical_attribution(source: str, license_str: str) -> str:
    return f"{source} licensed under {license_str}"


def find_meta_files() -> list[Path]:
    """data/anatomy/{slug}/meta_{view}.json 찾기. 부위당 다중 view 지원."""
    return sorted(anatomy_dir().glob("*/meta_*.json"))


def load_meta(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_meta(path: Path, meta: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")


def validate_schema(meta: dict, schema: dict) -> list[str]:
    """jsonschema가 있으면 사용, 없으면 기본 체크만."""
    errors: list[str] = []
    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft7Validator(schema)
        for err in validator.iter_errors(meta):
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"schema: {path}: {err.message}")
    except ImportError:
        # fallback: 필수 필드만 확인
        for field in schema.get("required", []):
            if field not in meta:
                errors.append(f"missing required field: {field}")
    return errors


def validate_file_path(meta: dict, base: Path) -> list[str]:
    errors: list[str] = []
    fp = meta.get("file_path", "")
    if not fp:
        return ["file_path empty"]
    target = base / fp
    if not target.exists():
        errors.append(f"file_path not found on disk: {fp}")
        return errors
    ext = target.suffix.lower().lstrip(".")
    if ext not in {"svg", "png"}:
        errors.append(f"file extension not in whitelist (svg/png): {ext}")
    return errors


def validate_slug_consistency(meta: dict, slugs: dict) -> list[str]:
    errors: list[str] = []
    slug = meta.get("body_part_slug", "")
    parts = slugs["parts"]
    if slug not in parts:
        errors.append(f"body_part_slug not in _SLUGS.json: {slug}")
        return errors
    expected_ko = parts[slug]["ko"]
    if meta.get("body_part_ko") != expected_ko:
        errors.append(
            f"body_part_ko mismatch: expected {expected_ko!r}, got {meta.get('body_part_ko')!r}"
        )
    # file_path가 slug 디렉토리 안에 있는지
    fp = meta.get("file_path", "")
    if fp and f"data/anatomy/{slug}/" not in fp:
        errors.append(f"file_path not under data/anatomy/{slug}/: {fp}")
    return errors


def validate_asset_id_pattern(meta: dict) -> list[str]:
    """asset_id가 anatomy_{slug}_{view}_v{N}이고 slug/view와 일치하는지."""
    errors: list[str] = []
    asset_id = meta.get("asset_id", "")
    slug = meta.get("body_part_slug", "")
    view = meta.get("view_angle", "")
    if not asset_id.startswith(f"anatomy_{slug}_{view}_v"):
        errors.append(
            f"asset_id pattern mismatch: expected anatomy_{slug}_{view}_v<N>, got {asset_id}"
        )
    return errors


def check_attribution(meta: dict, fix: bool) -> tuple[list[str], bool]:
    """attribution_text가 canonical과 일치하는지. --fix 시 자동 수정.

    Returns:
        (errors, modified)
    """
    source = meta.get("source", "")
    license_str = meta.get("license", "")
    expected = build_canonical_attribution(source, license_str)
    actual = meta.get("attribution_text", "")
    if actual == expected:
        return [], False
    if fix:
        meta["attribution_text"] = expected
        return [], True
    return [
        f"attribution_text mismatch:\n"
        f"      expected: {expected!r}\n"
        f"      actual:   {actual!r}\n"
        f"      hint: --fix로 자동 수정"
    ], False


def check_duplicate_asset_ids(metas: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    errors: list[str] = []
    for m in metas:
        aid = m.get("asset_id", "")
        if not aid:
            continue
        slug = m.get("body_part_slug", "?")
        if aid in seen:
            errors.append(f"duplicate asset_id {aid!r}: {seen[aid]} vs {slug}")
        else:
            seen[aid] = slug
    return errors


def compute_progress(slugs: dict) -> tuple[int, int, list[str]]:
    """완료 부위 카운트 + 미완 slug 리스트.

    부위 단위 진행률: 부위 디렉토리에 meta_*.json 1개라도 있으면 done.
    여러 view 자료를 받아도 부위 1개로 카운트.
    """
    parts = slugs["parts"]
    total = len(parts)
    done: list[str] = []
    pending: list[str] = []
    for slug in parts:
        slug_dir = anatomy_dir() / slug
        if slug_dir.is_dir() and any(slug_dir.glob("meta_*.json")):
            done.append(slug)
        else:
            pending.append(slug)
    return len(done), total, pending


def run(fix: bool = False, strict: bool = False, verbose: bool = False) -> int:
    schema = load_schema()
    slugs = load_slugs()
    base = repo_root()

    metas: list[dict] = []
    failures: dict[str, list[str]] = {}
    fixed_count = 0

    for meta_path in find_meta_files():
        slug = meta_path.parent.name
        # 부위당 여러 메타 — 키는 "slug/meta_view.json" 형태로 unique
        key = f"{slug}/{meta_path.name}"
        try:
            meta = load_meta(meta_path)
        except json.JSONDecodeError as e:
            failures[key] = [f"invalid JSON: {e}"]
            continue
        except Exception as e:
            failures[key] = [f"load error: {e}"]
            continue

        # --fix 모드: attribution을 schema 검증 전에 먼저 수정 (minLength 등 우회)
        attr_errors, attr_modified = check_attribution(meta, fix=fix)
        if attr_modified:
            save_meta(meta_path, meta)
            fixed_count += 1

        errors: list[str] = []
        errors += validate_schema(meta, schema)
        errors += validate_file_path(meta, base)
        errors += validate_slug_consistency(meta, slugs)
        errors += validate_asset_id_pattern(meta)
        errors += attr_errors

        if errors:
            failures[key] = errors
        metas.append(meta)

    dup_errors = check_duplicate_asset_ids(metas)

    # 출력
    done, total, pending = compute_progress(slugs)
    pct = (done / total * 100) if total else 0
    print(f"\n진행률: {done}/{total} ({pct:.1f}%)")

    if verbose or pending:
        if pending:
            preview = ", ".join(pending[:7])
            more = f" (외 {len(pending) - 7})" if len(pending) > 7 else ""
            print(f"  미완료: {preview}{more}")

    if fixed_count:
        print(f"\n🔧 자동 수정: {fixed_count} 부위 attribution_text")

    if failures or dup_errors:
        print("\n❌ 검증 실패:")
        for key, errs in failures.items():
            print(f"  [{key}]")
            for e in errs:
                print(f"    - {e}")
        for e in dup_errors:
            print(f"  [duplicate] {e}")
        return 1

    if not metas:
        print("\n⚠ meta.json이 한 개도 없음. fetch_anatomy.py 또는 init_anatomy_part.py로 시작.")
        return 0 if not strict else 1

    if strict and done < total:
        print(f"\n❌ --strict 모드: 30/30 완료 필요 (현재 {done}/{total})")
        return 1

    print("\n✓ 검증 통과")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="해부학 DB Phase 1 — meta.json 검증"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="attribution_text 자동 수정",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="30/30 완료 시만 exit 0 (CI용)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="미완료 부위 항상 표시",
    )
    args = parser.parse_args(argv)
    return run(fix=args.fix, strict=args.strict, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
