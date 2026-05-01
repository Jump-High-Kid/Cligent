#!/usr/bin/env python3
"""
해부학 DB Phase 1 — URL 기반 자동 다운로드 + 메타 추출 (메인).

Servier Medical Art / AnatomyTOOL / Wikimedia Commons 페이지 URL을 받아:
  1. Playwright headless로 페이지 fetch
  2. 이미지(SVG/PNG) 다운로드 → data/anatomy/{slug}/source.{ext}
  3. 페이지에서 title, license, attribution 자동 파싱
  4. meta.json 자동 생성 + init_anatomy_part 통합

사용 예:
    python scripts/fetch_anatomy.py neck_anterior \\
        --url "https://smart.servier.com/smart_image/neck-3/" \\
        --view anterior

Playwright 미설치 또는 fetch 실패 시:
    --manual 플래그로 init_anatomy_part.py와 동일 동작 (URL만 기록).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# 같은 scripts/ 디렉토리의 init_anatomy_part 재사용
sys.path.insert(0, str(Path(__file__).resolve().parent))
from init_anatomy_part import (  # type: ignore  # noqa: E402
    LICENSE_URLS,
    SOURCE_DEFAULT_LICENSE,
    VALID_VIEWS,
    build_meta,
    repo_root,
    write_meta,
)


def detect_source(url: str) -> str:
    """URL 도메인으로 source 식별."""
    host = urlparse(url).netloc.lower()
    if "servier" in host:
        return "Servier Medical Art"
    if "anatomytool" in host:
        return "AnatomyTOOL"
    if "wikimedia" in host or "wikipedia" in host:
        return "Wikimedia Commons"
    return "Other"


def parse_servier_page(html: str) -> dict:
    """Servier Medical Art 페이지에서 메타 정보 추출.

    실제 사이트 구조에 맞춰 selector 조정 필요. 현재는 일반 패턴 기반:
    - <title> 태그
    - meta tag (og:title, og:image)
    - CC license 표기 텍스트
    """
    info: dict = {}
    # title
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        info["title"] = m.group(1).strip()
    # og:image (이미지 URL)
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        info["image_url"] = m.group(1).strip()
    # CC 라이선스 감지
    if re.search(r"CC\s*BY\s*4\.0|creativecommons\.org/licenses/by/4\.0", html, re.IGNORECASE):
        info["license"] = "CC BY 4.0"
    elif re.search(r"CC\s*BY-SA\s*4\.0", html, re.IGNORECASE):
        info["license"] = "CC BY-SA 4.0"
    return info


def fetch_page(url: str, timeout_ms: int = 30000) -> tuple[str, Optional[bytes], Optional[str]]:
    """Playwright headless로 페이지 fetch.

    Returns:
        (html_text, image_bytes, image_url) — 이미지 못 찾으면 (None, None).

    Raises:
        ImportError: playwright 미설치.
        RuntimeError: fetch 실패.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError(
            "playwright 미설치. 설치:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            html = page.content()

            # og:image 또는 첫 SVG/PNG link 찾기
            info = parse_servier_page(html)
            image_url = info.get("image_url")
            image_bytes = None

            if image_url:
                # 이미지 fetch (page context의 쿠키 공유)
                resp = ctx.request.get(image_url, timeout=timeout_ms)
                if resp.ok:
                    image_bytes = resp.body()
            return html, image_bytes, image_url
        finally:
            browser.close()


def determine_extension(image_url: Optional[str], image_bytes: Optional[bytes]) -> str:
    """이미지 확장자 결정. svg/png만 허용."""
    if image_url:
        url_lower = image_url.lower().split("?")[0]
        if url_lower.endswith(".svg"):
            return "svg"
        if url_lower.endswith(".png"):
            return "png"
    # bytes magic 검사
    if image_bytes:
        if image_bytes[:4] == b"\x89PNG":
            return "png"
        if image_bytes.lstrip()[:5] == b"<?xml" or image_bytes.lstrip()[:4] == b"<svg":
            return "svg"
    # 기본값
    return "svg"


def fetch_and_save(
    slug: str,
    url: str,
    view: str,
    source_override: Optional[str] = None,
    license_override: Optional[str] = None,
    overwrite: bool = False,
    manual: bool = False,
) -> dict:
    """fetch + meta.json 작성. 결과 dict 반환."""
    source = source_override or detect_source(url)
    license_str = license_override or SOURCE_DEFAULT_LICENSE.get(source, "CC BY 4.0")

    image_saved_path = None
    parsed_info: dict = {}

    if not manual:
        try:
            html, image_bytes, image_url = fetch_page(url)
            parsed_info = parse_servier_page(html)
            if parsed_info.get("license"):
                # 페이지에서 감지된 라이선스가 우선 (사용자 override 없을 때)
                if license_override is None:
                    license_str = parsed_info["license"]

            if image_bytes:
                ext = determine_extension(image_url, image_bytes)
                target_dir = repo_root() / "data" / "anatomy" / slug
                target_dir.mkdir(parents=True, exist_ok=True)
                # view 접미사로 같은 부위 다중 자료 지원
                image_target = target_dir / f"source_{view}.{ext}"
                image_target.write_bytes(image_bytes)
                image_saved_path = str(image_target.relative_to(repo_root()))
        except ImportError as e:
            print(f"⚠ {e}", file=sys.stderr)
            print("⚠ --manual 모드로 폴백 (URL만 기록)", file=sys.stderr)
            manual = True
        except RuntimeError as e:
            print(f"⚠ fetch 실패: {e}", file=sys.stderr)
            print("⚠ --manual 모드로 폴백 (URL만 기록)", file=sys.stderr)
            manual = True

    meta = build_meta(
        slug=slug,
        view=view,
        source=source,
        license_str=license_str,
        source_url=url,
    )

    # 이미지 못 받았으면 file_path는 placeholder (사용자가 수동 복사)
    if image_saved_path:
        meta["file_path"] = image_saved_path

    # 페이지 title을 notes에 보존 (참고용)
    if parsed_info.get("title"):
        meta["notes"] = f"page title: {parsed_info['title']}"

    target = write_meta(slug, meta, overwrite=overwrite)
    return {
        "meta_path": str(target.relative_to(repo_root())),
        "image_saved": image_saved_path,
        "manual_mode": manual,
        "asset_id": meta["asset_id"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="해부학 DB Phase 1 — URL 기반 자동 다운로드"
    )
    parser.add_argument("slug", help="부위 영문 slug")
    parser.add_argument("--url", required=True, help="자료 페이지 URL")
    parser.add_argument(
        "--view",
        default="anterior",
        choices=sorted(VALID_VIEWS),
    )
    parser.add_argument(
        "--source",
        default=None,
        choices=sorted(SOURCE_DEFAULT_LICENSE.keys()),
        help="강제 source 지정 (default: URL에서 자동 감지)",
    )
    parser.add_argument(
        "--license",
        dest="license_str",
        default=None,
        choices=sorted(LICENSE_URLS.keys()),
        help="강제 라이선스 (default: 페이지에서 감지 또는 source 기본값)",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Playwright 사용 안 함, URL만 메타에 기록",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 meta.json 덮어쓰기",
    )

    args = parser.parse_args(argv)

    try:
        result = fetch_and_save(
            slug=args.slug,
            url=args.url,
            view=args.view,
            source_override=args.source,
            license_override=args.license_str,
            overwrite=args.force,
            manual=args.manual,
        )
    except (ValueError, FileExistsError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"❌ 예기치 못한 에러: {e}", file=sys.stderr)
        return 1

    print(f"✓ 생성 완료: {result['meta_path']}")
    print(f"  asset_id: {result['asset_id']}")
    if result["image_saved"]:
        print(f"  이미지 저장: {result['image_saved']}")
    else:
        print("⚠ 이미지 자동 다운로드 안 됨")
        print(f"  수동 복사: cp <자료 파일> data/anatomy/{args.slug}/source.svg")
    print(f"\n검증: python scripts/validate_anatomy_meta.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
