# Cligent 디자인 시스템

> **이 문서의 역할**: Cligent의 모든 페이지·모듈이 따라야 하는 단일 진실원(single source of truth).
> 새 모듈을 추가하거나 기존 페이지를 수정할 때 이 문서의 토큰·컴포넌트·패턴을 그대로 따른다.
> 변경 이력은 `docs/CHANGELOG.md` 참조.

---

## 0. 기준 파일 (canonical)

| 패턴 | canonical 파일 | 용도 |
|---|---|---|
| 앱 쉘 + 사이드바 | `templates/app.html` | 모든 페이지를 iframe으로 감싸는 쉘 |
| 일반 페이지 | `templates/dashboard.html` | 카드 그리드형 페이지 (재고·스케줄·고객 관리 등) |
| 챗 UI | `templates/blog_chat.html` | 메시지·옵션 칩·입력바 패턴 (콘텐츠 생성형) |

**원칙**: 새 페이지는 일반 패턴 또는 챗 UI 패턴 중 하나를 선택해 그대로 복제. 자체 디자인 도입 금지.

---

## 1. 디자인 토큰

### 1-1. 색상

```css
/* 주색 */
--emerald-900: #064e3b;   /* 활성 메뉴, 사용자 메시지 배경, primary 버튼, 헤드라인 */
--emerald-700: #047857;   /* primary hover */
--emerald-50:  #ecfdf5;   /* 강조 배경 */

/* 보조색 (sage 계열) */
--sage:        #a8b5a0;   /* 워드마크 도트, 보더 강조 */
--sage-soft:   #eef1ea;   /* 칩 보더, hover 배경 */
--sage-tint:   #f6f8f4;   /* AI 메시지 배경, 시스템 메시지 배경 */

/* 중성 (stone 계열) */
--stone-100:   #f5f5f4;   /* 사이드바 배경, 비활성 메뉴 hover, 토글 배경 */
--stone-300:   #d6d3d1;   /* disabled 상태 */
--stone-500:   #78716c;   /* 비활성 텍스트, 캡션 */
--stone-700:   #44403c;   /* 보조 텍스트 */
--stone-900:   #1c1917;   /* 본문 텍스트 */
--white:       #ffffff;   /* 카드 배경, 입력창 배경 */

/* 상태 */
--red-500:     #ef4444;   /* 알림 뱃지, 위험 액션 */
--amber-500:   #f59e0b;   /* 경고 (예산 80% 등) */
```

**Tailwind 매핑**: `bg-emerald-900`, `text-stone-600`, `hover:bg-stone-200` 등 Tailwind 기본 클래스 그대로 사용 가능. CSS 변수는 `templates/blog_chat.html` 같이 vanilla CSS 페이지에서만 사용.

### 1-2. 타이포그래피

| 용도 | 폰트 | 크기·굵기 |
|---|---|---|
| 본문 | Pretendard Variable | 16px / 400 / line-height 1.6 |
| 본문 강조 | Pretendard Variable | 16px / 500 |
| 헤드라인 (페이지 타이틀) | Manrope or Pretendard | 24px / 700 / letter-spacing -0.02em |
| 워드마크 (Cligent) | Manrope | 16px / 700 / letter-spacing -0.02em |
| 사이드바 메뉴 | Pretendard | 14px / 500 |
| 캡션·라벨 | Pretendard | 12~13px / 400 / `--stone-500` |
| 버튼 | Pretendard | 14px / 500 |

**CDN 로드 (모든 페이지 head 필수)**:
```html
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css" rel="stylesheet"/>
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css" rel="stylesheet"/>
```

### 1-3. 아이콘 (Material Symbols Outlined)

```html
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
```

```css
.material-symbols-outlined {
  font-variation-settings: 'FILL' 0, 'wght' 300, 'GRAD' 0, 'opsz' 24;
}
```

**활성 메뉴는 FILL 1**:
```css
.active .material-symbols-outlined {
  font-variation-settings: 'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 24;
}
```

### 1-4. 공간·반경·그림자

| 토큰 | 값 |
|---|---|
| 라운드 small | `8px` (입력 필드, 작은 버튼) |
| 라운드 medium | `12px` (시스템 메시지, 작은 카드) |
| 라운드 large | `16px` (말풍선, 모달, 일반 카드) |
| 라운드 pill | `999px` (칩, 라운드 버튼) |
| 메시지 간격 | `gap: 20px` |
| 카드 패딩 | `24px` 또는 `32px` |
| 페이지 좌우 여백 | `16px` (모바일) / `24px` (데스크톱) |
| neumorphic 그림자 | `box-shadow: 10px 10px 20px #e1ddd5, -10px -10px 20px #ffffff` |

### 1-5. 모션

```css
--duration-fast:   150ms;   /* 색상 변화, hover */
--duration-normal: 220ms;   /* 사이드바 토글, 패널 슬라이드 */
--ease-out: cubic-bezier(.4, 0, .2, 1);
```

---

## 2. 레이아웃 골격

### 2-1. 앱 쉘 + iframe 구조

```
사용자 → /app → app.html (사이드바 + iframe 컨테이너)
                ├─ 사이드바 (좌측 18rem, 모바일 숨김)
                └─ iframe → /dashboard, /blog, /settings 등
```

- `app.html`만 사이드바를 가진다.
- 하위 페이지(`dashboard.html`, `blog_chat.html`, `settings.html` 등)는 **자체 사이드바를 가지면 안 된다**.
- 각 하위 페이지는 iframe 감지 코드로 자기 사이드바를 숨겨 단독 접속 시에도 일관성 유지:

```html
<script>
  if (window.self !== window.top) {
    const sidebar = document.getElementById('sidebar');
    const main = document.getElementById('main-content');
    if (sidebar) sidebar.style.display = 'none';
    if (main) main.style.marginLeft = '0';
  }
</script>
```

### 2-2. 데스크톱 (≥768px)

```
┌──────────────────────────────────────────────┐
│ [사이드바 18rem]  │  [본문 영역]              │
│  · 로고 + 토글    │  ┌──────────────────────┐ │
│  · 메뉴 (8개)     │  │ 페이지 헤더           │ │
│  · 역할 뱃지      │  │                      │ │
│  · 직원 초대      │  │ 콘텐츠               │ │
│  · 로그아웃       │  │                      │ │
└────────────────────┴──────────────────────────┘
```

- 사이드바 width: `18rem` (`#sidebar`)
- collapsed 시: `4.5rem` (라벨·로고텍스트·역할뱃지·초대라벨 숨김)
- 본문 margin-left: 사이드바 width와 동기화

### 2-3. 모바일 (≤767px)

```
┌──────────────────────────────────────────────┐
│ [본문 영역, margin-left: 0]                  │
│                                              │
│  콘텐츠                                      │
│                                              │
├──────────────────────────────────────────────┤
│ [하단 4탭] 대시보드 / 블로그 / 공지 / 설정    │
└──────────────────────────────────────────────┘
```

- 사이드바: `display: none !important;`
- 하단 nav: `#mobile-nav` 4탭 고정
- iframe height: `calc(100dvh - 64px)` (하단 nav 64px 제외)
- 활성 탭: `color: #064e3b` + 아이콘 FILL 1

---

## 3. 사이드바 사양 (canonical: `app.html`)

### 3-1. 메뉴 목록

| 메뉴 | Material Symbol | 경로 | 상태 |
|---|---|---|---|
| 대시보드 | `dashboard` | `/dashboard` | 완성 |
| 블로그 생성기 | `article` (또는 `edit_square`) | `/blog` | 완성 |
| AI 도우미 | `chat` | `/chat` | 베타 비활성 |
| 재고 관리 | `inventory_2` | `#` | Coming Soon |
| 스케줄 관리 | `calendar_today` | `#` | Coming Soon |
| 고객 관리 | `group` | `#` | Coming Soon |
| 설정 | `settings` | `/settings` | 완성 |
| 도움말 | `help_outline` | `/help` | 완성 |

### 3-2. 메뉴 스타일

```html
<!-- 활성 -->
<a class="flex items-center gap-x-3 py-3 px-4 rounded-xl bg-emerald-900 text-white" href="/dashboard">
  <span class="material-symbols-outlined">dashboard</span>
  <span class="nav-label text-[0.875rem]">대시보드</span>
</a>

<!-- 비활성 -->
<a class="flex items-center gap-x-3 py-3 px-4 rounded-xl text-stone-600 hover:bg-stone-200 transition-colors" href="/blog">
  <span class="material-symbols-outlined">article</span>
  <span class="nav-label text-[0.875rem]">블로그 생성기</span>
</a>

<!-- Coming Soon -->
<a class="flex items-center gap-x-3 py-3 px-4 rounded-xl text-stone-400 cursor-not-allowed" href="#">
  <span class="material-symbols-outlined">inventory_2</span>
  <span class="nav-label text-[0.875rem]">재고 관리</span>
  <span class="nav-label ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-stone-200 text-stone-400">준비중</span>
</a>
```

### 3-3. collapsed 동작

- localStorage `cligent_sidebar`: `'1'`(접힘) / `'0'`(펼침)
- collapsed 시 숨김 클래스: `.nav-label`, `.sidebar-logo-text`, `.sidebar-role`, `.sidebar-invite-label`
- 토글 버튼: `menu_open` ↔ `menu` 아이콘 스왑

### 3-4. 하단 영역

순서 고정:
1. `role-badge` (모든 사용자)
2. `invite-btn` (director 이상만 노출)
3. `doLogout()` 버튼

### 3-5. iOS 토글 CSS (재사용)

```css
.ios-toggle { /* 체크박스 본체 (hidden) */ }
.ios-toggle-track { /* 배경 트랙 */ }
.ios-toggle-dot { /* 흰색 동그라미 */ }

/* 주의: '+' 아닌 '~' 셀렉터 사용 */
.ios-toggle:checked ~ .ios-toggle-dot { transform: translateX(20px); }
.ios-toggle:checked ~ .ios-toggle-track { background: var(--emerald-900); }
```

---

## 4. 공통 컴포넌트 카탈로그

### 4-1. 버튼

| 종류 | 클래스 / 스타일 |
|---|---|
| Primary | `bg-emerald-900 text-white rounded-xl py-2.5 px-5 font-medium hover:bg-emerald-700` |
| Secondary | `bg-white border border-sage-soft text-emerald-900 rounded-xl py-2.5 px-5 font-medium hover:bg-sage-soft` |
| Ghost | `text-stone-600 hover:bg-stone-100 rounded-xl py-2 px-3` |
| Danger | `bg-white border border-red-500 text-red-500 rounded-xl py-2.5 px-5 hover:bg-red-50` |
| Icon button | `w-9 h-9 rounded-lg hover:bg-stone-100 text-stone-500` |

### 4-2. 카드

```html
<!-- 일반 카드 -->
<div class="bg-white rounded-2xl p-6 border border-stone-200">
  <h3 class="text-lg font-bold text-stone-900 mb-4">제목</h3>
  <p class="text-stone-600">본문</p>
</div>

<!-- neumorphic 카드 (대시보드 통계) -->
<div class="neumorphic-card rounded-2xl p-6">...</div>
```

```css
.neumorphic-card {
  background: #ffffff;
  box-shadow: 10px 10px 20px #e1ddd5, -10px -10px 20px #ffffff;
}
```

### 4-3. 입력 필드

```html
<input class="w-full bg-white border border-stone-300 rounded-xl px-4 py-3
              text-[15px] focus:outline-none focus:ring-2 focus:ring-emerald-900/30
              focus:border-emerald-900"/>
```

- placeholder 색상: `text-stone-400`
- disabled: `bg-stone-100 text-stone-500`
- error 상태: `border-red-500`

### 4-4. 모달

```html
<div class="fixed inset-0 z-50 hidden items-center justify-center bg-black/40 backdrop-blur-sm">
  <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 p-8">
    <div class="flex justify-between items-center mb-6">
      <h3 class="text-lg font-bold">제목</h3>
      <button class="hover:bg-stone-100 p-1 rounded-full">
        <span class="material-symbols-outlined text-stone-500">close</span>
      </button>
    </div>
    <!-- 본문 -->
  </div>
</div>
```

- 백드롭: `bg-black/40 backdrop-blur-sm`
- 모달 max-width: `max-w-md` (448px) 또는 `max-w-lg` (512px)
- z-index: `50` (사이드바와 동일)

### 4-5. 뱃지

| 종류 | 스타일 |
|---|---|
| 역할 뱃지 | `px-3 py-1 rounded-xl bg-stone-200 text-[12px] font-medium text-stone-700` |
| 알림 뱃지 (Coming Soon) | `text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-stone-200 text-stone-400` |
| 안 읽음 도트 | `w-2 h-2 rounded-full bg-red-500` |
| 상태 (성공) | `bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full text-[12px]` |

### 4-6. 토스트

- 위치: 우상단 또는 우하단
- 성공: 초록 좌측 보더 + 체크 아이콘
- 실패: 빨강 좌측 보더 + X 아이콘
- 자동 사라짐: 3초

---

## 5. 페이지 패턴

새 페이지를 만들 때 아래 두 패턴 중 하나를 선택해 그대로 복제한다.

### 5-A. 일반 페이지 패턴 (canonical: `dashboard.html`)

**용도**: 카드 그리드, 통계, 폼, 목록 — 데이터 표시·조작 중심.

**골격**:
```html
<body class="bg-surface text-on-surface flex min-h-screen">
  <aside id="sidebar">...</aside>  <!-- iframe 내에서는 JS로 숨김 -->

  <main id="main-content" class="flex-1 ml-72 px-6 py-8">
    <!-- 페이지 헤더 -->
    <header class="mb-8">
      <h1 class="text-2xl font-bold text-stone-900">페이지 제목</h1>
      <p class="text-stone-500 mt-1">설명 한 줄</p>
    </header>

    <!-- 콘텐츠: 카드 그리드 또는 폼 -->
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      <div class="bg-white rounded-2xl p-6">...</div>
    </div>
  </main>
</body>
```

**구성 규칙**:
- 헤더는 항상 페이지 최상단, 제목 + 1줄 설명
- 카드 grid는 모바일 1열 → md 2열 → lg 3열 (콘텐츠 성격에 따라 조정)
- 본문 좌우 여백: `px-6` (데스크톱) / `px-4` (모바일)

### 5-B. 챗 UI 패턴 (canonical: `blog_chat.html`)

**용도**: 단계별 입력, AI와의 대화, 옵션 칩 선택 — 인터랙션 중심.

**골격**:
```
┌──────────────────────────────────────────┐
│ [헤더 56px] 워드마크 / 단계 표시 / 한도   │
├──────────────────────────────────────────┤
│                                          │
│ [메시지 영역, max-width: 768px, 중앙]    │
│  · AI 메시지 (sage-tint 배경, 좌측)       │
│  · 사용자 메시지 (emerald-900 배경, 우측) │
│  · 시스템 메시지 (sage-tint, 중앙, 작음)  │
│  · 옵션 칩 (sage-soft 보더, pill)        │
│                                          │
├──────────────────────────────────────────┤
│ [입력 영역] textarea + 원형 send 버튼    │
└──────────────────────────────────────────┘
```

**핵심 토큰**:
```css
--bubble-radius:    16px;
--bubble-max-width: 85%;
--bubble-gap:       20px;
--chip-radius:      999px;
--chip-height:      36px;
--input-radius:     24px;
--input-min-height: 48px;
--header-height:    56px;
```

**메시지 색상**:
- AI (assistant): 배경 `--sage-tint`, 텍스트 `--stone-900`
- 사용자 (user): 배경 `--emerald-900`, 텍스트 `--white`, font-weight 500
- 시스템 (system): 배경 `--sage-tint`, 텍스트 `--stone-700`, 13px, 중앙 정렬

**옵션 칩**:
- 기본: `--sage-soft` 보더 + 흰 배경 + `--emerald-900` 텍스트
- 추천: `--sage-tint` 배경 + `--sage-soft` 보더
- hover: 먹 번짐 효과 (`radial-gradient` + `ink-bleed` 키프레임)

**입력창**:
- 라운드: 24px
- min-height: 48px, max-height: 144px (4줄)
- focus: 보더 `--emerald-900`
- send 버튼: 원형 48×48, `--emerald-900` 배경

**모바일 키보드 대응**: `visualViewport` API로 `--app-height` CSS 변수 동적 갱신.

---

## 6. 폼 패턴

### 6-1. 라벨 + 입력

```html
<div class="mb-4">
  <label class="block text-[13px] font-medium text-stone-700 mb-2">한의원 이름</label>
  <input class="w-full bg-white border border-stone-300 rounded-xl px-4 py-3"/>
  <p class="text-[12px] text-stone-500 mt-1">사이드바와 블로그에 표시됩니다</p>
</div>
```

### 6-2. 자동저장 인디케이터

- 변경 → 디바운스 500ms → API 호출 → 우상단 토스트 "저장됨"
- 실패 시: 빨간 토스트 "저장 실패, 다시 시도" + 3초 후 자동 재시도 1회
- **저장 버튼은 두지 않는 것이 기본**. 명시적 commit이 필요한 경우(파괴적 액션, 비밀번호 변경)에만 사용.

### 6-3. 위험 액션

```html
<section class="border border-red-300 rounded-2xl p-6">
  <h3 class="text-lg font-bold text-red-600 mb-2">위험 영역</h3>
  <p class="text-stone-600 mb-4">이 작업은 되돌릴 수 없습니다.</p>
  <button class="border border-red-500 text-red-500 rounded-xl px-4 py-2 hover:bg-red-50">
    삭제
  </button>
</section>
```

- 삭제 확인: 한의원명 또는 "삭제" 단어 직접 타이핑 후에만 활성화

---

## 7. 새 모듈 추가 체크리스트

새 페이지·모듈을 추가할 때 아래를 모두 확인:

### 7-1. 백엔드
- [ ] `src/routers/<domain>.py`에 라우터 추가 (main.py가 아닌 routers/에 분리)
- [ ] `src/main.py`의 `app.include_router()`에 등록 (등록 순서 주의: 더 구체적인 path를 먼저)
- [ ] 권한 체크: `src/dependencies.py`의 `is_admin_clinic`, `require_admin_*` 활용

### 7-2. 사이드바
- [ ] `templates/app.html` `<nav>` 메뉴 추가 + `data-path` 속성
- [ ] Material Symbol 선택 (Coming Soon이면 `text-stone-400 cursor-not-allowed` + 준비중 뱃지)
- [ ] 모바일 하단 4탭에 추가할지 결정 (4탭은 가장 자주 쓰는 것만)

### 7-3. HTML 템플릿
- [ ] 5-A(일반) 또는 5-B(챗 UI) 패턴 중 선택해 복제
- [ ] head에 Pretendard + Material Symbols CDN 로드
- [ ] iframe 감지 스니펫 포함 (자체 사이드바 숨김)
- [ ] 디자인 토큰만 사용. 임의 색상·폰트 도입 금지

### 7-4. 디자인 일관성
- [ ] 활성 메뉴 색: `bg-emerald-900 text-white`
- [ ] 폰트: Pretendard Variable (본문) + Manrope (헤드라인 선택적)
- [ ] 아이콘: Material Symbols `wght 300, FILL 0`
- [ ] 라운드: small 8px / medium 12px / large 16px / pill 999px
- [ ] 모바일 ≤767px에서 사이드바 숨김 + 하단 nav 노출 검증

### 7-5. 권한·롤
- [ ] director 이상만 보일 항목은 `DIRECTOR_ROLES` 배열로 토글
- [ ] `chief_director` 전용은 별도 명시
- [ ] 모듈 권한 토글이 필요하면 `_require_module_permission` 패턴 따름

---

## 8. 금지 사항

다음은 **모두 디자인 일관성을 깨는 안티패턴**이다. PR에서 발견되면 거부.

### 8-1. 색상
- ❌ 임의 hex 색상 도입 (예: `#3b82f6`, `#8b5cf6`)
- ❌ Tailwind 외 색상 (slate, zinc 등 다른 중성 계열 — stone만 사용)
- ❌ 다크 모드 색상 도입 (현재 라이트 톤 단일)

### 8-2. 폰트
- ❌ 시스템 폰트 fallback 단독 사용 (반드시 Pretendard 로드)
- ❌ 3종 이상 폰트 패밀리 (Pretendard + Manrope 2종 한도)
- ❌ 임의 font-family 도입

### 8-3. 레이아웃
- ❌ 페이지 자체 사이드바 추가 (앱 쉘 사이드바와 충돌)
- ❌ iframe 감지 스니펫 누락 (단독 접속 시 사이드바 중복)
- ❌ 모바일에서 데스크톱 사이드바 노출

### 8-4. 컴포넌트
- ❌ Bootstrap·MUI·Ant Design 등 외부 UI 라이브러리 도입
- ❌ neumorphic 그림자를 다크 배경에 적용 (라이트 전용)
- ❌ `box-shadow` 임의 값 도입 (위 토큰 사용)

### 8-5. AI 슬롭 패턴
- ❌ 의미 없는 그라디언트 블롭
- ❌ "심플하고 깔끔한" 카드 그리드만 반복
- ❌ 호버·포커스·액티브 상태 미설계
- ❌ 위계 없이 균일한 padding·shadow

---

## 9. 변경 시 영향 범위

이 문서를 수정하면 **반드시 다음 파일도 함께 갱신**해야 한다:

- `templates/app.html` — 사이드바 변경 시
- `templates/dashboard.html` — 일반 페이지 토큰 변경 시
- `templates/blog_chat.html` — 챗 UI 토큰 변경 시
- `templates/settings.html` — 폼 패턴 변경 시
- `CLAUDE.md`의 디자인 시스템 포인터 — 이 문서 위치가 바뀔 때

토큰 일치 여부는 PR에서 수동 검증 (자동화 테스트 없음).
