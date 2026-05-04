"""scripts/patch_template_fonts.py

외부 폰트 CDN(Google Fonts, jsdelivr) 제거 + 셀프 호스팅 fonts.css 통일.

대상: templates/*.html (재귀)
변환:
  1. fonts.googleapis.com Material Symbols / Manrope link 제거
  2. jsdelivr Pretendard CSS link 모두 제거
  3. fonts.googleapis.com / fonts.gstatic.com / jsdelivr.net preconnect 제거
  4. <head>에 공통 블록 삽입:
       <link rel="preload" as="font" type="font/woff2" .../>
       <link rel="stylesheet" href="/static/fonts.css"/>
  5. <html lang="ko"> → <html lang="ko" class="cligent-booting">
  6. <head> 마지막에 ready 토글 인라인 script 삽입

idempotent: 두 번 실행해도 안전 (이미 적용된 파일은 변경 0).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"

PRELOAD_BLOCK = '''<link rel="preload" as="font" type="font/woff2" href="/static/fonts/PretendardVariable.woff2" crossorigin>
<link rel="preload" as="font" type="font/woff2" href="/static/fonts/material-symbols-outlined.woff2" crossorigin>
<link href="/static/fonts.css" rel="stylesheet"/>'''

READY_SCRIPT = '''<script>document.addEventListener('DOMContentLoaded',function(){requestAnimationFrame(function(){document.documentElement.classList.add('cligent-ready');});});</script>
<noscript><style>html.cligent-booting{visibility:visible !important;}</style></noscript>'''

# 제거 대상 패턴 — 공백·따옴표 변이 허용
REMOVE_PATTERNS = [
    # Google Fonts Material Symbols / Manrope (모든 variant)
    re.compile(r'\s*<link[^>]*fonts\.googleapis\.com/css2\?family=Material[^>]*>\s*\n?', re.IGNORECASE),
    re.compile(r'\s*<link[^>]*fonts\.googleapis\.com/css2\?family=Manrope[^>]*>\s*\n?', re.IGNORECASE),
    # Google Fonts 일반 (preconnect 등)
    re.compile(r'\s*<link[^>]*rel=["\']preconnect["\'][^>]*fonts\.googleapis\.com[^>]*>\s*\n?', re.IGNORECASE),
    re.compile(r'\s*<link[^>]*rel=["\']preconnect["\'][^>]*fonts\.gstatic\.com[^>]*>\s*\n?', re.IGNORECASE),
    re.compile(r'\s*<link[^>]*fonts\.gstatic\.com[^>]*>\s*\n?', re.IGNORECASE),
    # jsdelivr Pretendard 모든 variant
    re.compile(r'\s*<link[^>]*jsdelivr\.net/gh/orioncactus/pretendard[^>]*>\s*\n?', re.IGNORECASE),
    re.compile(r'\s*<link[^>]*rel=["\']preconnect["\'][^>]*cdn\.jsdelivr\.net[^>]*>\s*\n?', re.IGNORECASE),
]

# 이미 패치된 파일 인식 마커
PATCHED_MARKER = '/static/fonts.css'


def patch_file(path: Path) -> bool:
    """1 파일 패치. 변경 발생 시 True."""
    src = path.read_text(encoding='utf-8')
    original = src

    # 1) 외부 폰트 CDN link 제거
    for pat in REMOVE_PATTERNS:
        src = pat.sub('\n', src)

    # 1b) 기존 ready/booting 인라인 (재실행 시 갱신 위해 제거)
    src = re.sub(
        r"\s*<script>[^<]*cligent-ready[^<]*</script>\s*\n?", '\n', src,
    )
    src = re.sub(
        r"\s*<noscript><style>html\.cligent-booting\{[^<]*</style></noscript>\s*\n?",
        '\n', src,
    )

    # 2) 이미 패치되어 있으면 PRELOAD_BLOCK 재삽입 skip
    needs_preload = PATCHED_MARKER not in src

    # 3) <head> 직후에 PRELOAD_BLOCK 삽입
    if needs_preload:
        m = re.search(r'(<head[^>]*>)', src, re.IGNORECASE)
        if m:
            insert_at = m.end()
            src = src[:insert_at] + '\n' + PRELOAD_BLOCK + src[insert_at:]

    # 4) <html lang="..."> → class="cligent-booting" 추가 (이미 있으면 skip)
    def add_class(m: re.Match) -> str:
        attrs = m.group(1)
        if 'cligent-booting' in attrs:
            return m.group(0)
        # class 속성이 이미 있으면 거기에 추가, 없으면 신규
        if re.search(r'class\s*=\s*["\']', attrs):
            attrs = re.sub(
                r'class\s*=\s*(["\'])([^"\']*)\1',
                lambda mm: f'class={mm.group(1)}cligent-booting {mm.group(2)}{mm.group(1)}',
                attrs, count=1,
            )
        else:
            attrs += ' class="cligent-booting"'
        return f'<html{attrs}>'

    src = re.sub(r'<html([^>]*)>', add_class, src, count=1, flags=re.IGNORECASE)

    # 5) </head> 직전에 READY_SCRIPT 삽입
    src = re.sub(
        r'(</head>)', f'{READY_SCRIPT}\n\\1', src, count=1, flags=re.IGNORECASE,
    )

    # 6) 다중 빈 줄 정리 (3+ → 2)
    src = re.sub(r'\n{3,}', '\n\n', src)

    if src != original:
        path.write_text(src, encoding='utf-8')
        return True
    return False


def main() -> None:
    files = sorted(TEMPLATES.rglob('*.html'))
    changed = 0
    for f in files:
        if patch_file(f):
            print(f'[patched] {f.relative_to(ROOT)}')
            changed += 1
        else:
            print(f'[skip]    {f.relative_to(ROOT)}')
    print(f'\n변경: {changed}/{len(files)}')


if __name__ == '__main__':
    main()
