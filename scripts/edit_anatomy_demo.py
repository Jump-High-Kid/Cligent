#!/usr/bin/env python3
"""
해부학 자료 변형 데모 — gpt-image-1.5 edit endpoint 직접 호출.

흐름:
    1. data/anatomy/{slug}/source_{view}.png 읽기
    2. 변형 프롬프트로 OpenAI edit 호출 (Standard 1024×1024 medium)
    3. 결과 base64 → data/anatomy/{slug}/_demo/{stage}_{view}.png

사용 예:
    # 4번 라벨 제거
    python scripts/edit_anatomy_demo.py shoulder --stage no_labels --views anterior posterior

    # 1번 한글 라벨 (별도 가이드 dict 필요 — 추후)
    python scripts/edit_anatomy_demo.py shoulder --stage korean_labels --views anterior

    # 2번 사실적 사진 톤
    python scripts/edit_anatomy_demo.py shoulder --stage photo_realistic --views anterior

원본은 절대 건드리지 않음. 결과는 _demo/ 폴더에만 저장.
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# .env 자동 로드 (SECRET_KEY가 필요 — secret_manager에서 OpenAI 키 복호화)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from ai_client import (  # noqa: E402
    AIClientError,
    call_openai_image_edit,
    call_openai_image_generate,
)


# stage별 (mode, view→prompt) 정의.
# mode = "edit" → call_openai_image_edit (base 이미지 필요)
# mode = "generate" → call_openai_image_generate (텍스트 프롬프트만)
STAGE_CONFIG: dict[str, dict] = {
    "no_labels": {
        "mode": "edit",
        "prompts": {
            "_default": (
                "Remove all text labels, arrows, and annotations from this "
                "anatomical diagram. Keep the anatomical structure, colors, "
                "line work, and shapes exactly as they are. Output a clean "
                "medical illustration with NO text, NO arrows, NO leader "
                "lines — only the pure anatomical drawing. Preserve the "
                "same illustration style and color palette."
            ),
        },
    },
    "photo_realistic": {
        "mode": "edit",
        "prompts": {
            "_default": (
                "Convert this stylized anatomical diagram into a "
                "photorealistic medical illustration in the style of a "
                "high-end anatomy atlas (Netter Atlas standard). Keep the "
                "exact same anatomical structures, proportions, and "
                "viewpoint. Use realistic muscle, tendon, bone, and "
                "connective tissue textures. Soft natural lighting, "
                "anatomically accurate colors. Remove any "
                "cartoon/diagrammatic appearance. No text labels."
            ),
        },
    },
    "korean_labels": {
        # gpt-image-2 generations로 새로 그리기 (base 이미지 input 불가).
        # Claude가 원본 보고 만든 묘사 + 한글 라벨 위치를 텍스트로 명시.
        "mode": "generate",
        "prompts": {
            "anterior": (
                "Anatomical diagram of human right shoulder, anterior view, "
                "clean medical illustration style with flat colors and "
                "clear outlines on white background. "
                "Beige bones: clavicle horizontal at top, scapula triangular "
                "on left, humerus cylindrical on right side. "
                "Pink rotator cuff muscles wrapping around the joint capsule. "
                "Light purple bursa above the joint. Light blue biceps "
                "tendon descending. Dark teal glenohumeral joint capsule. "
                "Thin dark teal leader lines pointing from anatomical "
                "structures to short Korean Hangul text labels positioned "
                "around the diagram on the white margin (NOT overlapping "
                "the body). "
                "Korean Hangul labels (Korean alphabet ONLY — NOT Chinese "
                "characters, NOT Japanese kana, NOT Latin/English letters): "
                "'견봉' top-left, '쇄골' top-right, 'AC관절' top-center, "
                "'상완골' right side, '견갑골' bottom-left, '회전근개' "
                "center on the muscles. "
                "Black sans-serif Korean text in clean modern font similar "
                "to Pretendard. Single standalone image, do not combine "
                "into grid/mosaic/collage. WHO Standard Anatomy reference, "
                "anatomically accurate, medical illustration accuracy."
            ),
            "posterior": (
                "Anatomical diagram of human right shoulder, posterior view, "
                "clean medical illustration style on white background. "
                "Beige bones: clavicle and acromion at top, scapula with "
                "scapular spine prominent on left, humerus on right. "
                "Pink rotator cuff muscles (supraspinatus above the spine, "
                "infraspinatus and teres minor below) fanning across the "
                "scapula. Light purple bursa. "
                "Thin leader lines to short Korean Hangul labels "
                "(Korean ONLY, NOT Chinese, NOT Japanese, NOT English): "
                "'견봉' top-center, '쇄골' top-left, '견갑극' middle-left, "
                "'상완골' right, '견갑골' bottom, '회전근개' center on "
                "muscles. Black Pretendard-style Korean sans-serif. "
                "Single standalone image."
            ),
        },
    },
}


def stage_config(stage: str, view: str) -> tuple[str, str]:
    """returns (mode, prompt)."""
    if stage not in STAGE_CONFIG:
        raise SystemExit(
            f"unknown stage: {stage!r}\nvalid: {sorted(STAGE_CONFIG.keys())}"
        )
    cfg = STAGE_CONFIG[stage]
    prompts = cfg["prompts"]
    prompt = prompts.get(view) or prompts.get("_default")
    if not prompt:
        raise SystemExit(
            f"no prompt for stage={stage!r} view={view!r}. "
            f"available views: {sorted(prompts.keys())}"
        )
    return cfg["mode"], prompt


def run_one(
    slug: str, view: str, stage: str, size: str, quality: str
) -> Path:
    """단일 view 변형/생성 → _demo/{stage}_{view}.png 저장. 결과 경로 반환."""
    mode, prompt = stage_config(stage, view)

    if mode == "edit":
        src = ROOT / "data" / "anatomy" / slug / f"source_{view}.png"
        if not src.exists():
            raise SystemExit(f"원본 없음: {src}")
        image_bytes = src.read_bytes()
        print(f"  [{view}] mode=edit, 원본 {len(image_bytes):,} bytes — 호출 중...")
        try:
            results = call_openai_image_edit(
                image_bytes=image_bytes,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
        except AIClientError as e:
            raise SystemExit(f"❌ edit 실패 [{view}]: {e}")
    elif mode == "generate":
        print(f"  [{view}] mode=generate, prompt {len(prompt)} chars — 호출 중...")
        try:
            results = call_openai_image_generate(
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
        except AIClientError as e:
            raise SystemExit(f"❌ generate 실패 [{view}]: {e}")
    else:
        raise SystemExit(f"unknown mode: {mode!r}")

    if not results or not results[0].content:
        raise SystemExit(f"❌ 빈 응답 [{view}]")

    out_dir = ROOT / "data" / "anatomy" / slug / "_demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stage}_{view}.png"
    out_path.write_bytes(base64.b64decode(results[0].content))
    print(f"  [{view}] ✓ 저장: {out_path.relative_to(ROOT)} ({out_path.stat().st_size:,} bytes)")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="해부학 자료 변형 데모")
    parser.add_argument("slug", help="부위 slug (예: shoulder)")
    parser.add_argument(
        "--stage",
        required=True,
        choices=sorted(STAGE_CONFIG.keys()),
        help="변형 단계",
    )
    parser.add_argument(
        "--views",
        nargs="+",
        default=["anterior"],
        choices=["anterior", "posterior", "lateral", "medial", "oblique"],
    )
    parser.add_argument(
        "--size",
        default="1024x1024",
        choices=["1024x1024", "1024x1536", "1536x1024"],
    )
    parser.add_argument(
        "--quality",
        default="medium",
        choices=["low", "medium", "high"],
    )
    args = parser.parse_args(argv)

    print(f"🎨 변형 데모 — slug={args.slug} stage={args.stage} "
          f"size={args.size} quality={args.quality}")
    for view in args.views:
        run_one(args.slug, view, args.stage, args.size, args.quality)
    print("\n✓ 완료. 결과는 _demo/ 폴더 확인.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
