"""
image_modules.py — 이미지 모듈 11종 (Cligent 베타, 2026-05-01 신설)

목적:
  - 무관한 negatives·directives를 모든 장면에 일괄 주입하던 구조 제거.
  - 각 이미지 유형(모듈)별로 필요한 fragment만 활성화 → gpt-image-2 충실도 ↑
    → 재생성 횟수 ↓ → 진짜 비용 lever.

확장 방식:
  - 각 모듈 dict는 self-contained — `directives`, `negatives`, `boosters`, `style_suffix` 만 채우면 됨.
  - 새 모듈 추가 = IMAGE_MODULES[12] = {...} 한 줄. 호출 코드는 무변경.
  - 사용자(원장님)가 직접 fragment 보강 가능.

스키마:
  - id (1~11): 정수 ID. Stage 1 분석 JSON의 scene.module 값과 일치.
  - name_ko: 한국어 모듈명 (UI·로그용).
  - directives: 영어 fragment list — gpt-image-2 본문 프롬프트에 추가될 지시.
  - negatives: 영어 fragment list — 그 모듈에만 의미 있는 네거티브.
  - boosters: 강도 부스터 list (선택) — ultra-detailed / DSLR 등.
  - style_suffix: 후미 분위기·조명 표현 (선택).

Midjourney 파라미터(--ar, --stylize, --style raw 등) 일체 사용 안 함 — gpt-image-2가 무시.
"""

from __future__ import annotations

from typing import Optional


# ── 11 모듈 카탈로그 ─────────────────────────────────────────


IMAGE_MODULES: dict[int, dict] = {
    1: {
        "name_ko": "해부학 이미지",
        "directives": [
            "isolated on pure white background, no background, clean cutout",
            "original anatomical proportions preserved, uniform scale only, no stretching, no distortion",
            "based on actual medical anatomy references (WHO Standard Acupuncture Point Locations, Netter Atlas standard), no creative reinterpretation",
            "color variations limited to adjacent hues within same color family, preserve standard medical illustration colors (skin tone, muscle red, bone ivory, nerve yellow)",
        ],
        "negatives": [
            "background scenery, room interior, gradient background, shadow background, textured background",
            "stylistic distortion of bone or muscle structure",
            "extra fingers, missing fingers, fused fingers, asymmetric digit count",
            "elbow joint on leg, knee on arm, mirrored anatomy errors",
            "text overlay on image, hangul or korean text rendering, unintended labels",
        ],
        "boosters": [
            "ultra-detailed",
            "anatomically accurate human anatomy, correct joint structure",
            "medical illustration accuracy",
        ],
        "style_suffix": "even neutral lighting, museum-quality medical reference",
    },
    2: {
        "name_ko": "인체 치료 이미지(침/뜸/약침)",
        "directives": [
            # 가운 — Western 표준 lab coat 명시 (한국 한의사 가운 = 서양식 흰 가운)
            "doctor wearing single-breasted Western-style white lab coat with stand collar, "
            "Korean medical institution standard, Hangul name badge embroidered on the chest, "
            "modern professional fit (NOT Chinese tunic suit, NOT mandarin collar, NOT changshan robe)",
            # 환자복
            "Korean patient gown in solid muted color (light sky blue or pale green), "
            "top-bottom separated, neat tied closures, modern Korean hospital style",
            # 시술 표현
            "skin exposed at acupoint location, needle inserted into bare skin (not through clothing)",
            # 의료 기구 — 한국 클리닉 표준 명시
            "stainless steel kidney-shaped tray with individually sealed disposable acupuncture needle packages "
            "(Korean clinic standard, modern sterilization), thin single-use needles "
            "(NOT Chinese reusable thick needles, NOT ornate handle needles)",
            # 배경 — 한국 인테리어 + 한글 게시물
            "blurred modern Korean clinic interior, light oak wood floor, soft warm beige walls, "
            "framed Korean medical certificates in Hangul on the wall, "
            "subtle Korean clinic atmosphere "
            "(NO Chinese calligraphy scrolls, NO red lanterns, NO gold dragon motifs, "
            "NO Japanese tatami flooring, NO Chinese herbal wall posters)",
        ],
        "negatives": [
            "needle through clothing, acupuncture needle on fabric, casual clothes during acupuncture",
            # 가운 — 명시적 China/Japan 차단
            "Chinese tunic suit, Chinese mandarin collar coat, changshan style robe, "
            "frog-button closure coat, traditional Chinese physician gown",
            "Japanese kampo robe, Japanese yukata-style gown",
            # 의료 기구
            "Chinese reusable thicker needles, Chinese fire cupping with glass jars, "
            "ornate decorative needle handles, traditional Chinese herbal trays",
            # 배경 — 명시적 China/Japan 차단
            "Chinese TCM clinic decor, Chinese herbal jars on shelves, red lanterns, "
            "gold dragon motifs, Chinese calligraphy wall scrolls, Chinese palace style room",
            "Japanese tatami flooring, Japanese kanji wall scrolls, Japanese shoji screens",
            # 환자/윤리
            "patient face front close-up, medical records visible, prescription visible",
            # 해부학
            "extra fingers, missing fingers, fused fingers, asymmetric digit count",
            "elbow joint on leg, knee on arm",
            # 텍스트
            "Chinese simplified characters on signage, Japanese shinjitai text, "
            "Chinese seal scripts, mandarin pinyin signage",
            "text overlay on image, hangul or korean text rendering, unintended labels",
            # 침 과도 — 시술 안전 표현
            "multiple needles in unsafe pattern, dozens of needles, needle field, "
            "needles all over body",
            # 양방 응급실 톤 차단
            "over-exposed bright flash, harsh hospital fluorescent lighting, "
            "emergency room atmosphere, ICU equipment visible",
            # 의료 윤리 — 아동 무단 묘사 차단
            "child patient, infant, toddler (unless explicitly age-specified)",
            # 환자 노출 차단 — 침 부위 외 신체 노출
            "nudity, exposed underwear, exposed chest or buttocks, inappropriate skin exposure",
        ],
        "boosters": [
            "ultra-detailed",
            "anatomically accurate human anatomy, correct joint structure",
            "DSLR photography",
        ],
        "style_suffix": "soft natural lighting, calm healing atmosphere",
    },
    3: {
        "name_ko": "추나치료 이미지",
        "directives": [
            # 가운 — 모듈 2와 동일 명시
            "doctor wearing single-breasted Western-style white lab coat with stand collar, "
            "Korean medical institution standard, Hangul name badge "
            "(NOT Chinese tunic suit, NOT mandarin collar, NOT changshan robe)",
            # 환자복
            "Korean patient gown in solid muted color, top-bottom separated, modern Korean hospital style",
            # 의료 기구 — Chuna 테이블 한국 표준
            "Korean Chuna therapy adjustable padded vinyl clinic table "
            "(beige or muted green color, Korean clinic standard, modern frame), "
            "patient lying prone with proper draping "
            "(NOT Chinese tuina table with face-hole only, NOT Japanese shiatsu floor mat)",
            # 배경
            "modern Korean clinic interior, soft warm beige walls, light oak wood floor, "
            "framed Korean medical certificates in Hangul on wall "
            "(NO Chinese calligraphy scrolls, NO red lanterns, NO gold dragon motifs, "
            "NO Japanese tatami flooring)",
        ],
        "negatives": [
            "Chinese tuina massage table (face-hole only), Japanese shiatsu floor mat",
            "Chinese tunic suit, Chinese mandarin collar coat, changshan style robe, "
            "traditional Chinese physician gown",
            "Japanese kampo robe, Japanese yukata-style gown",
            "patient face front close-up",
            "extra fingers, missing fingers, asymmetric digit count",
            "Chinese TCM clinic decor, red lanterns, Chinese calligraphy wall scrolls, "
            "gold dragon motifs, Chinese palace style room",
            "Japanese tatami flooring, Japanese kanji wall scrolls, Japanese shoji screens",
            "text overlay on image, hangul or korean text rendering, unintended labels",
            "over-exposed bright flash, harsh hospital fluorescent lighting, "
            "emergency room atmosphere",
            "child patient, infant, toddler (unless explicitly age-specified)",
            "nudity, exposed underwear, exposed chest or buttocks, inappropriate skin exposure",
        ],
        "boosters": [
            "anatomically accurate human anatomy, correct joint structure",
            "DSLR photography",
        ],
        "style_suffix": "soft natural lighting, calm healing atmosphere",
    },
    4: {
        "name_ko": "한약·음식 이미지",
        "directives": [
            # 한약 분기 — Stage 1 subject 영문에 'herbal' 류 키워드 있을 때
            "Korean herbal medicine in sealed paper pouches (한지 약봉지) or vacuum-packed clean sachets",
            "Korean stainless steel decoction pot (한약 전탕기), modern compact design",
            # 음식 분기 — Stage 1 subject 영문에 'meal/food/dish/cuisine' 류 있을 때
            "healthy Korean cuisine, traditional Korean ingredients, natural seasonal foods",
            "clean modern food styling, overhead flat lay or 45-degree angle",
            "simple ceramic or wooden tableware",
        ],
        "negatives": [
            "Chinese ceramic herb jars, Chinese herbal market open bins, Chinese clay teapot",
            "Chinese herbal scroll posters",
            "Japanese kampo packaging, Japanese kanji wall scrolls",
            "human face close-up, hands holding food in unsanitary way",
            "raw bloody meat, gore",
            "text overlay on image, hangul or korean text rendering, unintended labels",
        ],
        "boosters": ["DSLR photography"],
        "style_suffix": "warm natural lighting, clean composition",
    },
    5: {
        "name_ko": "포스터·카드 출력 이미지",
        "directives": [
            "editorial poster design, magazine-quality composition",
            "bold typography space (text area reserved), clean grid layout",
            "high contrast composition, focal point centered or rule-of-thirds",
        ],
        "negatives": [
            "cluttered background, multiple competing focal points",
            "unintended text overlay, garbled letters",
        ],
        "boosters": ["editorial photography quality"],
        "style_suffix": "high-key lighting, magazine cover aesthetic",
    },
    6: {
        "name_ko": "한의학 도서(판본) 이미지",
        "directives": [
            "vintage Korean classical medical text (Donguibogam 동의보감, Donguisusebowon 동의수세보원)",
            "woodblock printed pages with traditional Korean Hanja (정자체)",
            "aged paper texture, ink calligraphy lines, antique book edge",
            "isolated or on simple neutral surface (light wood or beige fabric)",
        ],
        "negatives": [
            "Chinese simplified characters (简体字), Japanese shinjitai (新字体)",
            "modern printing, glossy paper, color illustrations",
            "Chinese palace medical scrolls, Japanese washi book style",
        ],
        "boosters": [
            "ultra-detailed",
            "DSLR photography close-up",
        ],
        "style_suffix": "soft directional lighting, museum archival aesthetic",
    },
    7: {
        "name_ko": "환자 상황 이미지",
        "directives": [
            "patient experiencing symptoms in everyday life setting (home, office, outdoor)",
            "side or three-quarter view, no front face close-up",
            "natural body posture conveying the symptom (without exaggeration)",
        ],
        "negatives": [
            "patient face front close-up",
            "exaggerated suffering, dramatic medical emergency",
            "extra fingers, missing fingers, asymmetric digit count",
            "text overlay on image, hangul or korean text rendering, unintended labels",
            "child patient, infant, toddler (unless explicitly age-specified)",
        ],
        "boosters": [
            "anatomically accurate human anatomy",
            "DSLR photography",
        ],
        "style_suffix": "natural ambient lighting, realistic everyday scene",
    },
    8: {
        "name_ko": "한의사·환자 상담 이미지",
        "directives": [
            # 가운
            "Korean medical doctor wearing single-breasted Western-style white lab coat with stand collar, "
            "Korean medical institution standard, Hangul name badge embroidered on the chest "
            "(NOT Chinese tunic suit, NOT mandarin collar, NOT changshan robe)",
            # 의료 기구·책상
            "modern light oak consultation desk, computer monitor with Korean Hangul interface visible, "
            "ergonomic office chair, simple desk organizer (Korean clinic standard)",
            # 배경
            "modern Korean medical consultation room, soft warm beige walls, "
            "framed Korean medical certificates in Hangul on the wall, "
            "soft natural lighting from window "
            "(NO Chinese calligraphy scrolls, NO traditional Chinese palace style decoration, "
            "NO red lanterns, NO Japanese shoji screens)",
            # 인물 배치
            "doctor and patient seated, side or three-quarter angle, calm professional rapport",
        ],
        "negatives": [
            "Chinese tunic suit, Chinese mandarin collar coat, changshan style robe, "
            "traditional Chinese physician gown",
            "Chinese hospital gown patterns, Japanese kampo robe, Japanese yukata-style gown",
            "stethoscope dominant, Western medical equipment focus, MRI scanner visible",
            "patient face front close-up",
            "Chinese TCM clinic decor, traditional Chinese palace style consultation room, "
            "red lanterns, gold dragon motifs, Chinese calligraphy wall art",
            "Japanese tatami flooring, Japanese kanji wall scrolls, Japanese shoji screens",
            "Chinese simplified characters on signage, Japanese shinjitai text",
            "text overlay on image, hangul or korean text rendering, unintended labels",
            "over-exposed bright flash, harsh hospital fluorescent lighting, "
            "emergency room atmosphere",
        ],
        "boosters": ["DSLR photography"],
        "style_suffix": "warm natural lighting, trustworthy professional atmosphere",
    },
    9: {
        "name_ko": "증상 특징 요약 이미지",
        "directives": [
            "infographic style, clean flat illustration, labeled diagram",
            "isolated on pure white or very light neutral background",
            "icons, arrows, or labels conveying symptom features clearly",
        ],
        "negatives": [
            "photorealistic photo, 3D render",
            "cluttered icons, overlapping text",
            "background scenery",
            "garbled letters, unreadable text",
        ],
        "boosters": ["medical illustration accuracy"],
        "style_suffix": "flat design, vector-style clean lines",
    },
    10: {
        "name_ko": "자세 비교 이미지",
        "directives": [
            "side-by-side posture comparison (correct vs incorrect, or before vs after)",
            "side or three-quarter view, full body or upper-body framing",
            "neutral background to focus attention on posture",
            "subtle visual markers (arrows, dotted lines) optional",
        ],
        "negatives": [
            "patient face front close-up",
            "extra fingers, missing fingers",
            "elbow joint on leg, knee on arm, mirrored anatomy errors",
            "text overlay on image, hangul or korean text rendering, unintended labels",
            "child patient, infant, toddler (unless explicitly age-specified)",
            "nudity, exposed underwear, exposed chest or buttocks, inappropriate skin exposure",
        ],
        "boosters": [
            "ultra-detailed",
            "anatomically accurate human anatomy, correct joint structure",
            "DSLR photography",
        ],
        "style_suffix": "even neutral lighting, study reference aesthetic",
    },
    11: {
        "name_ko": "기타",
        "directives": [
            "modern Korean oriental medicine clinic context",
            "calm professional aesthetic",
        ],
        "negatives": [
            "Chinese TCM clinic visual cues, Japanese kampo style",
            "Chinese simplified characters on signage",
            "text overlay on image, hangul or korean text rendering, unintended labels",
        ],
        "boosters": [],
        "style_suffix": "soft natural lighting",
    },
}


# ── 글로벌 가드 (모듈 무관 전체 적용) ─────────────────────────


GLOBAL_NEGATIVES: list[str] = [
    "watermark, signature, artist signature",
    "low quality, blurry result, JPEG artifacts",
    "NSFW, gore",
]

# 손/발 등장이 명시적으로 표시될 때 자동 추가 (anatomical_region == hand|foot)
HAND_FOOT_DIRECTIVES: list[str] = [
    "exactly five fingers and five toes per limb",
    "symmetric bilateral anatomy",
]

# 단일 이미지 강제 — 어떤 외부 AI에 보내도 그리드/모자이크 합성 방지
SINGLE_IMAGE_DIRECTIVE = (
    "generate as single standalone image, "
    "do not combine into a grid, mosaic, collage, or tiled layout"
)


# ── 헬퍼 ─────────────────────────────────────────────────────


def get_module(module_id: Optional[int]) -> dict:
    """1~11 범위 밖이면 모듈 11 (기타) fallback."""
    if module_id is None:
        return IMAGE_MODULES[11]
    if module_id in IMAGE_MODULES:
        return IMAGE_MODULES[module_id]
    return IMAGE_MODULES[11]


def total_modules() -> int:
    return len(IMAGE_MODULES)


def build_module_addendum(
    module_id: Optional[int],
    anatomical_region: Optional[str] = None,
) -> str:
    """모듈 ID + 부위 정보 → Stage 2 user message에 붙일 영문 fragment.

    호출 측은 이 문자열을 그대로 user message 뒤에 append.
    Stage 2 Claude가 이 fragment를 영문 프롬프트에 자연스럽게 통합.

    Args:
        module_id: 1~11 (None이면 11 fallback).
        anatomical_region: leg|arm|foot|hand|back|head|torso|none (선택).
            hand·foot이면 손가락·발가락 디렉티브 자동 추가.

    Returns:
        모듈 fragment 영문 텍스트 (없으면 빈 문자열).
    """
    module = get_module(module_id)
    lines: list[str] = []

    name_ko = module.get("name_ko", "")
    if name_ko:
        lines.append(f"## Module: {module_id} — {name_ko}")

    directives = module.get("directives") or []
    if directives:
        lines.append("Directives (include in prompt naturally):")
        for d in directives:
            lines.append(f"  - {d}")

    boosters = module.get("boosters") or []
    if boosters:
        lines.append("Quality boosters:")
        for b in boosters:
            lines.append(f"  - {b}")

    style_suffix = module.get("style_suffix") or ""
    if style_suffix:
        lines.append(f"Style suffix: {style_suffix}")

    negatives = module.get("negatives") or []
    if negatives:
        lines.append("Negatives (module-specific, prepend to negative_prompt):")
        for n in negatives:
            lines.append(f"  - {n}")

    if anatomical_region in ("hand", "foot") and HAND_FOOT_DIRECTIVES:
        lines.append("Hand/foot accuracy directives (add to prompt):")
        for d in HAND_FOOT_DIRECTIVES:
            lines.append(f"  - {d}")

    return "\n".join(lines)


def build_global_directives() -> str:
    """모든 모듈 공통 — Stage 2 system prompt 끝에 한 번만 붙이면 됨."""
    lines = ["## Global rules (always apply)"]
    lines.append(f"- {SINGLE_IMAGE_DIRECTIVE}")
    lines.append("Global negatives (always include):")
    for n in GLOBAL_NEGATIVES:
        lines.append(f"  - {n}")
    return "\n".join(lines)
