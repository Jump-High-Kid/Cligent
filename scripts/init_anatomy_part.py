#!/usr/bin/env python3
"""
해부학 DB Phase 1 — 빈 meta.json 자동 생성 (offline fallback).

Servier 외 소스(직접 그림, 동료 자료)를 사용할 때 또는 fetch_anatomy.py 실패 시 사용.

사용 예:
    python scripts/init_anatomy_part.py neck_anterior --view anterior
    python scripts/init_anatomy_part.py shoulder --view lateral --source "Wikimedia Commons"

자동 채움:
    asset_id, body_part_slug, body_part_ko, source, license, license_url,
    attribution_text, downloaded_at, file_path

수동 입력 필요:
    source_url
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# 라이선스 → URL 매핑 (검증 스크립트와 공유)
LICENSE_URLS = {
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC BY-SA 4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC BY 3.0": "https://creativecommons.org/licenses/by/3.0/",
}

# 소스별 기본 라이선스
SOURCE_DEFAULT_LICENSE = {
    "Servier Medical Art": "CC BY 4.0",
    "AnatomyTOOL": "CC BY 4.0",
    "Wikimedia Commons": "CC BY-SA 4.0",
    "Custom (in-house)": "CC0",
    "Other": "CC BY 4.0",
}

VALID_VIEWS = {"anterior", "posterior", "lateral", "medial", "oblique"}


def repo_root() -> Path:
    """프로젝트 루트 추정 (이 스크립트의 부모 디렉토리)."""
    return Path(__file__).resolve().parent.parent


def load_slugs() -> dict:
    path = repo_root() / "data" / "anatomy" / "_SLUGS.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_attribution(source: str, license_str: str) -> str:
    """canonical attribution_text 생성. validate가 동일 함수로 검증."""
    return f"{source} licensed under {license_str}"


def build_meta(
    slug: str,
    view: str,
    source: str,
    license_str: str,
    source_url: str = "",
    version: int = 1,
) -> dict:
    slugs_data = load_slugs()
    parts = slugs_data["parts"]

    if slug not in parts:
        raise ValueError(
            f"unknown slug: {slug!r}\n"
            f"valid slugs: {sorted(parts.keys())}"
        )
    if view not in VALID_VIEWS:
        raise ValueError(
            f"unknown view_angle: {view!r}\nvalid: {sorted(VALID_VIEWS)}"
        )
    if source not in SOURCE_DEFAULT_LICENSE:
        raise ValueError(
            f"unknown source: {source!r}\nvalid: {sorted(SOURCE_DEFAULT_LICENSE)}"
        )
    if license_str not in LICENSE_URLS:
        raise ValueError(
            f"license not in whitelist: {license_str!r}\nvalid: {sorted(LICENSE_URLS)}"
        )

    body_part_ko = parts[slug]["ko"]
    asset_id = f"anatomy_{slug}_{view}_v{version}"
    # 다중 view 지원: 부위당 여러 자료를 view_angle 접미사로 구분.
    file_path = f"data/anatomy/{slug}/source_{view}.svg"

    return {
        "asset_id": asset_id,
        "body_part_slug": slug,
        "body_part_ko": body_part_ko,
        "view_angle": view,
        "source": source,
        "source_url": source_url or "https://example.com/REPLACE_ME",
        "license": license_str,
        "license_url": LICENSE_URLS[license_str],
        "attribution_text": build_attribution(source, license_str),
        "downloaded_at": date.today().isoformat(),
        "file_path": file_path,
        "modifications": "",
        "acupoints": [],
        "notes": "",
    }


def write_meta(slug: str, meta: dict, overwrite: bool = False) -> Path:
    """meta_{view}.json 파일로 기록. view는 meta dict의 view_angle 필드에서 추출."""
    view = meta.get("view_angle", "")
    if view not in VALID_VIEWS:
        raise ValueError(f"meta missing valid view_angle: {view!r}")

    target_dir = repo_root() / "data" / "anatomy" / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"meta_{view}.json"

    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target.name} already exists: {target}\nuse --force to overwrite"
        )

    with target.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="해부학 DB Phase 1 — 빈 meta.json 자동 생성"
    )
    parser.add_argument("slug", help="부위 영문 slug (e.g., neck_anterior)")
    parser.add_argument(
        "--view",
        default="anterior",
        choices=sorted(VALID_VIEWS),
        help="시점 각도 (default: anterior)",
    )
    parser.add_argument(
        "--source",
        default="Servier Medical Art",
        choices=sorted(SOURCE_DEFAULT_LICENSE.keys()),
        help="자료 출처 (default: Servier Medical Art)",
    )
    parser.add_argument(
        "--license",
        dest="license_str",
        default=None,
        help="라이선스 (default: 출처별 자동)",
    )
    parser.add_argument(
        "--url",
        dest="source_url",
        default="",
        help="자료 페이지 URL (선택, 나중 수동 입력 가능)",
    )
    parser.add_argument(
        "--version",
        type=int,
        default=1,
        help="asset_id의 v{N} (default: 1)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 meta.json 덮어쓰기",
    )

    args = parser.parse_args(argv)

    license_str = args.license_str or SOURCE_DEFAULT_LICENSE[args.source]

    try:
        meta = build_meta(
            slug=args.slug,
            view=args.view,
            source=args.source,
            license_str=license_str,
            source_url=args.source_url,
            version=args.version,
        )
        target = write_meta(args.slug, meta, overwrite=args.force)
    except (ValueError, FileExistsError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    print(f"✓ 생성 완료: {target}")
    print(f"  asset_id: {meta['asset_id']}")
    if not args.source_url:
        print("⚠ source_url 미입력 — meta.json 열어서 직접 입력 필요")
    print(f"\n다음 단계:")
    print(f"  1. cp <자료 파일> data/anatomy/{args.slug}/source_{args.view}.svg")
    print(f"  2. python scripts/validate_anatomy_meta.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
