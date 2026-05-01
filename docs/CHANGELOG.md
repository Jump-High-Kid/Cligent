# Cligent CHANGELOG

CLAUDE.md에서 분리한 구현 이력 아카이브. 최신 항목이 위.
운영 중 동작/스키마는 코드와 CLAUDE.md를 단일 근거(SOT)로 본다 — 본 파일은 **참고용 히스토리**.

---

## 2026-05-01

### CLAUDE.md 슬림화 + 다중 view 인프라 + 어깨 첫 자료

**CLAUDE.md 슬림화**: 70KB / 1040줄 → 19.7KB / 349줄. 날짜별 구현 이력은 `docs/CHANGELOG.md`로 분리. 영구 아키텍처/현재 운영 정보만 유지.

**해부학 DB 다중 view 인프라 확장**:
- 부위당 1자료 → 여러 view 공존
- 파일명: `source.{ext}` → `source_{view}.{ext}`, `meta.json` → `meta_{view}.json`
- `_schema.json` file_path 정규식에 view 접미사 enum 추가
- `init_anatomy_part.py`: `build_meta()` file_path, `write_meta()` 출력 경로 갱신 (meta dict의 view_angle 필드 기반)
- `fetch_anatomy.py`: 이미지 저장 경로
- `validate_anatomy_meta.py`: `find_meta_files()` glob `*/meta_*.json`, `compute_progress()` 부위 단위 카운트(부위 디렉토리에 meta_*.json 1개라도 있으면 done), `failures` dict 키를 `slug/meta_view.json`으로 unique
- 테스트 50/50 통과 (이전 46 + 신규 4: multi-view coexist 시나리오)

**어깨 첫 자료 (1/30)**:
- 출처: Wikipedia File:Shoulder_joint.svg + File:Shoulder_joint_back-en.svg, CC BY-SA 4.0
- `data/anatomy/shoulder/source_{anterior,posterior}.png` + `meta_{anterior,posterior}.json`

**자료 변형 자동화 보류**:
- 사용자 결정: 현 시점 OpenAI API 변형 품질 < ChatGPT 웹 직접 변환 → 베타 후로 미룸
- `gpt-image-1.5` edit는 한글이 깨짐(사용자 직접 검증)
- `scripts/edit_anatomy_demo.py` 데모 스크립트 보존 (Phase 2 통합 시 출발점) — `no_labels`(edit), `photo_realistic`(edit), `korean_labels`(generate) 3 stage 지원
- `data/anatomy/*/_demo/` `.gitignore` 추가
- 메모리: `feedback_image_edit_tool_choice.md`, `project_anatomy_db_multi_view.md`

### 해부학 DB Phase 1 인프라 (후반)

베타 critical path. Cohort 1 노출 게이트 3조건 중 ②번. 30 부위 자료 수집 인프라 완성, 도메인 작업(원장님)은 1주 일정으로 시작.

**산출물 (8 파일 + 30 디렉토리):**
- `data/anatomy/_SLUGS.json` — 30 영문 slug ↔ 한글 ↔ 카테고리 single source of truth
- `data/anatomy/_schema.json` — JSON Schema Draft-07 (필수/선택/특수 필드 + enum + format 검증)
- `data/anatomy/_LICENSE_TEMPLATE.json` — 빈 메타 양식
- `data/anatomy/_SEARCH_URLS.json` — 30 부위 × 3소스(Servier/AnatomyTOOL/Wikimedia) 검색 링크 90개
- `data/anatomy/_README.md` — 작업 가이드
- `data/anatomy/{30 slug}/.gitkeep` — placeholder, 진행률 추적용
- `scripts/init_anatomy_part.py` — 빈 meta.json 자동 생성 (offline fallback)
- `scripts/fetch_anatomy.py` — Playwright 기반 URL → 다운로드 + 메타 자동 추출 (메인)
- `scripts/validate_anatomy_meta.py` — jsonschema 검증 + `--fix` + 진행률 + `--strict`

**의존성**: `playwright==1.48.0`, `jsonschema==4.23.0`

**원장님 작업 흐름 (1부위당 ~3분):**
```
1. _SEARCH_URLS.json에서 부위별 3 소스 링크 확인
2. 브라우저에서 적합 자료 1개 선택, 페이지 URL 복사
3. python scripts/fetch_anatomy.py {slug} --url "..." --view anterior
4. python scripts/validate_anatomy_meta.py
```

**메타 스키마**:
- 필수 10: asset_id, body_part_slug, body_part_ko, view_angle, source, source_url, license, attribution_text, downloaded_at, file_path
- 선택 4: license_url(자동 채움), modifications, acupoints, notes
- 특수 (이번 포함): view_angle (anterior/posterior/lateral/medial/oblique)
- 특수 (Phase 2 예약): mask_path, body_part_kcd

**asset_id 규칙**: `anatomy_{slug}_{view_angle}_v{N}`
**라이선스 화이트리스트**: CC BY 4.0 / CC BY-SA 4.0 / CC0 / CC BY 3.0
**attribution_text canonical**: `"{source} licensed under {license}"` — 미스매치 시 `--fix`

**테스트**: 46/46 통과. 회귀 0건.

**다음 단계**: Week 2 경혈 좌표 매핑 도구, Week 3~4 240 경혈 좌표 매핑, M1~M2 image2 edit endpoint + DB 통합.
**메모리**: `project_anatomy_db.md`

### 베타 직전 버그·기능 일괄 수정 (후반, 미커밋·테스트 대기)

**버그 수정:**
- 옵션 다중 선택 (`explanation_type` `multi:True` + `match_options_multi` 함수)
- 챗 입력창 스크롤바 hidden
- 유튜브 nav 비활성 ("준비중")
- 이미지 진행 시간 60초/장 표시 ("약 N분 남음")
- 한의사 가운 차이나칼라 차단 (`stand collar` → `notched lapel`)
- 인포그래픽 영어 출력 차단 (모듈 5·9 "Korean Hangul only")
- edit endpoint 모델 분리 — generations=`gpt-image-2` / edits=`gpt-image-1.5` (gpt-image-2 edit 미지원). BytesIO에 `.name="input.png"`/`.name="mask.png"` 부여 필수
- 참고 문헌 헤더 H2 승격 + RAG 0건 명시 안내
- 카운트다운 옵션 라벨 IMAGE_OPTIONS와 통일

**흐름 개선 (Stage.CONFIRM_IMAGE 추가):**
- TOPIC → LENGTH → QUESTIONS×4 → SEO → **CONFIRM_IMAGE** → GENERATING → IMAGE
- `state.auto_image=True` → 본문 완료 후 카운트다운 (3초 + meta.auto_action) → client setTimeout이 sendTurn('전체 만들기') 자동
- 갤러리 후 `kind:"completion_summary"` — 본문 복사·전체 다운로드·발행 확인 3 버튼

**사용량 카드 개편:**
- "첫 블로그까지" 카드 삭제
- 글·이미지 2 카드 — 이번달/플랜한도/진행바 + 누적 숫자
- 본인 클리닉 + 베타 가입일(`clinics.created_at`) 이후만 집계
- 코호트 1 standard 30/월 강제
- `/api/image/stats` 신규

**테스트**: pytest 358/365 (회귀 0). 7건 사전 등록 P2/P3.
**상세**: `project_beta_bugfixes_2026-05-01.md`

### 블로그 챗 UI 단일 진입점 통합 (Day 5.5)

`/blog` 라우트를 `templates/index.html`(4단계 폼)에서 `templates/blog_chat.html`(챗 UI)로 교체.
- `src/main.py:663` — `FileResponse(... "blog_chat.html")` 변경
- `src/main.py:691-697` — 중복 `/blog` 라우트 7줄 제거

**자동 활성화**: 헤더 quota 카운터 / 단일 PNG + 전체 ZIP 다운로드 (JSZip) / 카드별 [↺] / 이미지 편집 + quota / KPI 자동 추적

**회귀**: pytest 358/365. test_blog_chat_route.py 11/11.

**안전 확보 후 정리 대상 (Cohort 1 7일 안정 동작 후):**
1. `main.py:1910-1935` `/blog/chat` 라우트 제거 또는 308 redirect
2. `clinics.chat_beta_enabled` 컬럼 제거
3. `tests/test_blog_chat_route.py` 게이트 테스트 2건
4. `templates/index.html` 2394줄 dead code 삭제

### 이미지 모듈 시스템 + negative 통합

**11 모듈 분기 시스템 (`src/image_modules.py`):**
- 1 해부학 / 2 인체치료(침·뜸·약침) / 3 추나 / 4 한약·음식 / 5 포스터·카드 / 6 한의학 도서 / 7 환자 상황 / 8 상담 / 9 증상 특징 요약 / 10 자세 비교 / 11 기타
- 각 모듈 dict: `name_ko / directives / negatives / boosters / style_suffix`
- `get_module(id)`, `build_module_addendum(id, anatomical_region)`, `build_global_directives()`
- Stage 1이 scene별 `module: 1~11` 분류, Stage 2(254→80줄)가 모듈 fragment 통합
- **5장 모두 다른 모듈** — `blog_chat_flow.py`가 5번 직접 호출
- Midjourney 파라미터 전부 제거 — gpt-image-2가 무시

**negative_prompt 본문 통합 (CRITICAL):**
- gpt-image-2는 별도 `negative_prompt` 인자 없음
- `blog_chat_flow.py`가 본문 끝에 `\n\nNegative aspects to avoid: ...` 자동 합침
- env `IMAGE_INJECT_NEGATIVES=0`으로 즉시 끔

**가운·의료기구·배경 positive 강화 (모듈 2·3·8):**
- "single-breasted Western-style white lab coat with stand collar (Korean medical institution standard, Hangul name badge) — NOT Chinese tunic suit, NOT mandarin collar"

**5개 추가 negative 분배:** 한글 텍스트 박힘(1·2·3·4·7·8·10·11) / 침 과도(2) / 양방 응급실 톤(2·3·8) / 아동(2·3·7·10) / 노출(2·3·10). 모듈 5·6·9는 의도된 텍스트라 한글 negative 제외.

**이미지 진행 안내**: 5번 호출 사이사이 SSE stage_text 표시.

**기타**: `quality` 인자 제거(SDK 미지원), 옵션 단축키 4 → 9, 비용 표시 `is_admin`만, `---` → `<hr>`, 이미지 카드 클릭 lightbox.

**테스트**: 137/137.

### 옵션 카탈로그(QUESTIONS) + 카드별 [↺]

- `src/blog_chat_options.py` 신설 — 4 stage 옵션 카탈로그 → `to_blog_args()`
- LENGTH → QUESTIONS×4 → SEO 흐름
- 카드별 [↺] (n=1) — `image_generator.regenerate_set(prompt, plan_id, regen_used, n)`
- progress_only placeholder DOM 제거
- 스크롤바 완전 hidden

---

## 2026-04-30

### Phase 2~4 — 이미지 인프라

**Phase 2: ai_client + image_generator (커밋 f6eea8a)**
- `src/ai_client.py`: Anthropic + OpenAI 통합 래퍼
  - `call_anthropic_messages(model, system, user, cache_system=True)` — prompt caching
  - `call_openai_image_generate(prompt, size, quality, n)` — gpt-image-2 generations
  - `call_openai_image_edit(image_bytes, prompt, mask_bytes=None, n)`
  - 표준 `AIClientError` (auth/rate_limit/bad_request/timeout/server/unknown)
  - `asyncio.Semaphore(3)` OpenAI Tier1 rate limit 대응
  - OpenAI 키는 `secret_manager.get_server_secret("openai_api_key")`
- `src/image_generator.py`: 비즈니스 로직
  - 플랜별 한도: Standard 1+2 / Pro 2+4 / trial=Standard / free 0
  - 플랜별 해상도: Standard 1024×1024 medium / Pro 1536×1024 high
  - `generate_initial_set / regenerate_set / edit_image / get_quota_status`
  - `ImageQuotaExceeded` 예외

**Phase 3: prompt caching + Haiku 메타 (커밋 5b6ee1c)**
- `src/blog_generator.py`: system 프롬프트에 `cache_control: ephemeral`
  - 두 번째 호출부터 cache_read → 75~90% input 비용 절감
  - 비용 산정: cache_read 10%, cache_create 125%
- `src/metadata_generator.py`: Haiku 4.5로 메타 4종 추출
  - title (40자) / tags (5개) / summary (150자) / og_description (120자)
  - 5000자 초과 본문 압축 (앞 4000 + 끝 1000)
  - 비용: ₩30~50 → ₩2~5 (-85%)

**Phase 4: 이미지 세션 + API 라우트 (커밋 7294c47)**
- DB: `image_sessions` 테이블 (session_id UUID4, clinic_id, plan_id_at_start, regen_count, edit_count, created_at, last_active_at)
- `src/image_session_manager.py`: DB 레이어, PermissionError로 다른 클리닉 접근 차단
- API 라우트 4종:
  - `POST /api/image/generate-initial` — 5장 + 세션 발급
  - `POST /api/image/regenerate`
  - `POST /api/image/edit` — multipart 업로드
  - `GET /api/image/session/{id}`
- 429 응답에 `{kind: "quota_exceeded", type, plan_id, used, limit, message}`

**관리자 OpenAI 키 등록 (Phase 1)**
- `/admin/settings` OpenAI API 키 카드
- 검증: `client.models.list()` — 401 거부, 403 PermissionDenied(Restricted 키)는 통과
- 저장: Fernet 암호화 (SECRET_KEY 파생)

**테스트 합계**: 156/156.

### 사상체질 임상진료지침 1차 출처 격상 (후반)

**`prompts/references/sasang.txt`** 보강 (93→177줄):
- **1차 출처**: 사상체질의학회. (2022). 『사상체질병증 한의표준임상진료지침』 (정부 발간물)
- **2차 출처**: 이제마 동의수세보원 신축본(1901) — 처방 67방 원전
- **충돌 시 1차 우선** 명시

**섹션 5 — 권고사항 요약**: KCD-8 12개 중분류 / 진단·평가 / 공통 치료 / 특정 질환 (B/Moderate 2건, C/Low 10건, A 0건) / 예방 / 권고등급
**섹션 6 — 활용 규칙**: 권고등급 인용·KCD ICD 표현 권장 / A등급 임의·KCD 임의·"효과 입증" 단정 금지 / 1편당 1차 출처 1회 이상 필수

**옵시디언 위키 ingest**: raw PDF + sources/concepts md.

### 어드민 패널 P2 5종

**1. 신청자 라이프사이클 강화 (`/admin/applicants` 전면 재작성)**
- `beta_applicants` 9 컬럼 추가: `application_type`, `marketing_consent`, `consented_terms_version`(`v1.0-2026-04-29`), `ip_address`, `user_agent`, `rejection_reason`, `admin_notes`, `admin_tags`, `expires_at`
- 신규 테이블 `applicant_emails` — 모든 발송 결과 자동 ledger
- status enum 확장: pending / invited / registered / **rejected** / **expired**
- `/api/beta/apply` 강화: 약관 동의(필수: 개인정보 / 선택: 마케팅), `expires_at = now + 30일`
- lifespan `_applicant_expiry_scheduler` 24h
- 어드민 API 5종: stats / emails / PATCH notes·tags / reject / resend
- UI: 6 퍼널 카드, 행 펼침 timeline, 인라인 편집

**2. 로그인 이력 PIPA 90일 (`/admin/login-history` + 사용자 본인)**
- 신규 테이블 `login_history` + 인덱스 3종
- `auth_manager.authenticate_user` → `(user, failure_reason)` 튜플 (user_not_found / invalid_credentials / password_not_set / disabled)
- `record_login_attempt()` fail-soft
- lifespan `_login_history_purge_scheduler` 24h, 90일 초과 자동 삭제
- **의심 IP 자동 감지**(1시간 내 동일 IP 5회 이상 실패)
- 사용자 본인: `GET /api/auth/login-history` 최근 90일 50건
- **SQLite native datetime 비교** 필수: `datetime(created_at) >= datetime('now', ?)`

**3. 에러 로그 뷰어 (`/admin/errors`)**
- `data/error_logs/{date}.jsonl`, `observability.py:_write_error_log` (PII 마스킹)
- API 3종: dates / summary?days=7 / list with filters
- lifespan `_error_logs_purge_scheduler` 24h, 90일 초과 unlink

**4. 데일리 리포트 즉시 트리거 (`/admin/usage`)**
- 기존 Bearer-only → `_require_admin_or_session`로 확장 (CLI Bearer 호환)

**5. 전체 블로그 이력 (`/admin/blogs`)**
- `blog_history.save_blog_entry(..., clinic_id=None)` 옵션 인자
- `GET /api/admin/blogs?clinic_id=&q=&publish_status=&date_from=&date_to=&page=&per_page=`
- 발행 상태 4단계: none / pending / found / missing

**어드민 nav 통일 (8링크)**: 어드민 / 신청자 / 한의원 / 사용량 / 피드백 / 로그인 이력 / 에러 / 블로그 / 설정

**lifespan 스케줄러 6종 (24h 주기)**: 데일리 리포트 / E4 베타 리마인더 / 네이버 발행 확인 / **신청자 30일 만료** / **로그인 이력 90일 정리 (PIPA)** / **에러 로그 90일 정리**

---

## 2026-04-29

### 보안 헤더 (Caddyfile)
`/opt/homebrew/etc/Caddyfile` cligent.kr/cligent.co.kr 블록:
```
header {
    Strict-Transport-Security "max-age=300"
    X-Content-Type-Options "nosniff"
    Referrer-Policy "strict-origin-when-cross-origin"
    X-Frame-Options "SAMEORIGIN"
}
```
includeSubDomains·preload 미사용. 1주 후 86400, 1개월 후 31536000 단계 상향 예정.

### OG 이미지 (1200×630)
- 위치: `static/og-image.png` (180KB)
- 디자인: emerald(#064e3b) 배경 + 흰 글씨, "원장님의 시간을 돌려드립니다." (시간 sage underline) + "Medical AI Agent, Cligent"
- 생성: `/tmp/og.html` HTML+CSS → Chrome headless `--window-size=1200,630 --force-device-scale-factor=1`

### 랜딩 리뉴얼 1·2단계 + SEO 인프라

**1단계 카피·구조**: 한의원장→**원장님**, "선착순 5명 무료" 제거, Hero "원장님의 **시간**을 돌려드립니다", Solution 3 STEP→4 STEP, BYOAI 섹션 → "왜 Cligent인가" 비교(7항목), **로드맵 섹션 신설**(현재/다음/이후/비전).

**2단계 디자인**: **soft sage 보조 컬러** (`--sage:#a8b5a0`, `--sage-soft:#eef1ea`, `--sage-tint:#f6f8f4`), 배경 pure white, 타이포 위계 강화(h1 40~76px), 카드 elevation+hover, 비교표 강화(emerald 헤더 + ✓ prefix), 헤더 backdrop blur, "Beta" 배지, 섹션 padding 64→96px.

**SEO 인프라 (`src/main.py` + `templates/landing.html`)**:
- `GET /robots.txt` — 공개 페이지 허용, `/api/`·`/admin`·`/app`·`/dashboard`·`/blog`·`/chat`·`/settings`·`/onboard`·`/login`·`/forgot-password`·`/youtube`·`/help` 차단
- `GET /sitemap.xml` — 공개 4개 URL
- `landing.html` head 메타: canonical / keywords·author·robots / **Open Graph** 풀세트 / **Twitter Card** summary_large_image / **JSON-LD `@graph`** Organization + WebSite + SoftwareApplication
- **Google Search Console 인증** (토큰: `1W24HKtWNVkWebhUsc-IyUG6TjyeVVNm3WN1Tpwb8dg`)
- **Naver Search Advisor 인증** (토큰: `4f52868ae171c9987fd900323d156bc39f74b4d3`)

**알려진 이슈 — 모바일 Chrome**: cligent.kr이 모바일 Chrome 미접속 (데스크톱/모바일 Naver 정상). 가설: HTTP/3 QUIC이 ASUS UDP 443 미통과. 임시 해결: Caddy 전역 `protocols h1 h2` 또는 라우터 UDP 443 포트포워딩.

### 대시보드 실데이터 전환
- 환자 관리 목업(KPI 5장·차트·CRM·교육영상) 전부 제거
- 새 구성: 인사말+CTA / 사용량 3카드 / 최근 생성 블로그(10건) / 시리즈 주제 칩 / 최근 공지 1건
- 발행 상태 뱃지 4단계: 미등록 / 대기 중 / ✓ 발행 확인됨 / ! 누락
- `GET /api/blog/publish-status` 신규 — 일괄 반환
- 헤더 우측 아이콘 통일, 사람 아이콘 → `/settings#system`
- `settings.html` 해시 딥링크 추가

### 관리자 패널 P1
- **`_require_admin_or_session(request)`** 헬퍼 — 세션 OR `ADMIN_SECRET` Bearer
- 진입점: `/admin` 인덱스(5카드) + 사이드바·모바일 드로어 "관리자"(`is_admin`만)
- 기존 secret 모달·sessionStorage·Bearer 헤더 모두 제거 → 세션 쿠키
- 신규 페이지 3개:
  - `/admin/clinics` — 메타 수정 모달(plan_id/trial_expires_at/plan_expires_at)
  - `/admin/usage` — 이번 달 통계 + Top 50 클리닉
  - `/admin/feedback` — 필터 / NEW 뱃지 / viewed_at 토글
- DB 마이그: `feedback.viewed_at TEXT`

### 참고 문헌 검증 가능성 강화
- **문제**: 가상 논문 할루시네이션
- **수정 4단**:
  - A. RAG 항상 호출 (정보·전문 → 모든 모드). 0건이면 "학술 논문 형식 인용 작성 금지" 주입
  - B. `prompts/blog.txt` "절대 원칙: 가상 인용 생성 금지" 섹션, "4~6개" 강제 제거
  - C. `build_rag_context_for_prompt` 5대 규칙 (번호·메타·URL 그대로, 인용구는 초록만, 가상 금지)
  - D. `citation_provider.build_citation_block` "원전 / 가이드라인" + "추가 검색 링크" 두 그룹 분리

### 공지사항 게시판 (신설)
- DB: `announcements` / `announcement_reads` / `announcement_attachments`
- 라우트: `/announcements`, `/announcements/{id}`, `/new`·`/{id}/edit` (admin only)
- 본문: marked.js + DOMPurify 클라이언트 markdown 렌더, 이미지 5MB(jpg/png/webp/gif), `static/uploads/announcements/`
- 카테고리 3종(업데이트/점검/일반), 상단 고정, 안 읽은 뱃지
- **공지 작성 정책**: 외부 사용자용, 내부 구현 상세 제외. memory `feedback_announcement_scope.md`

### 모바일 UX 정비
- `/mobile` + UA sniff 제거 → 단일 진입점 `/app` 반응형
- 햄버거(좌상단) ↔ API 아이콘(우상단)
- 햄버거 → 우측 슬라이드 드로어 (도움말 / YouTube / 팀원 초대 / 관리자 / 로그아웃)
- 하단 nav 4탭: 대시보드 / 블로그(`edit_square`) / 공지(`campaign`) / 설정
- API 키 배너: 일일 닫기 정책 (`localStorage.cligent_apikey_banner_dismissed`)

### 랜딩 정비
- `templates/landing.html` 가격 정보 완전 제거: 네비 "가격" 링크, `#pricing` 섹션 51줄, FAQ "Standard 29,000원" 구체 가격
- FAQ 답변 → "정식 가격은 베타 운영 결과에 따라 추후 공지"

---

## 2026-04-28

### RAG 학술 검색 통합
- **신규**: `src/academic_search.py` — 3소스(jkom·PubMed·Naver doc) 병렬 검색 + 24h 디스크 캐시
- jkom.org: POST `/articles/search_result.php` + form data, 브라우저 UA + Referer 필수, urllib.request 사용 (httpx timeout)
- PubMed E-utilities: esearch.fcgi + efetch.fcgi, 한국어→영어 매핑 50+ 용어
- Naver doc.json: 권한 활성화 필요
- 캐시: `data/academic_cache.json` (24h TTL)
- 블로그 통합: `build_rag_context_for_prompt()`로 system_prompt 끝에 append. `rag_results` 있으면 동적 citation_block 스킵
- SSE status: "학술 자료를 검색하고 있습니다..."
- 가짜 인용 문제 해결: RISS·KCI 자동 조립 인용은 RAG 결과 있을 때 비활성
- 신규 의존성: `beautifulsoup4==4.12.3`
- 블로그 잘림 수정: `max_tokens` 4500 → 8000

### 네이버 발행 확인 + 자동 인링크
- **신규**: `src/naver_checker.py` — Naver Search API blog.json 폴링
- `data/pending_checks.json` 백그라운드 폴링 (60m → 120m×5 → 360m×4 → 720m, 7일 만료)
- API: `POST /api/blog/history/{entry_id}/publish-check`, `GET /api/blog/notifications`, `POST /api/blog/notifications/{id}/dismiss`
- 어드민: `templates/admin_settings.html` Naver Client ID/Secret 입력 (`data/app_settings.json`)
- 알림: 검색 적중 시 이메일 + 대시보드 배너

### 의료법 고지문 자동 삽입
- `_inject_legal_disclaimer()`: 의료법 56·57조, 시행령 23·24조 준수 문구를 byline 바로 위 자동 삽입

### 블로그 프롬프트 v0.4
- 체류시간 예고문 다양화 (8가지 형태)
- "참고 자료" → "참고 문헌" 통일
- 참고 문헌 4~6개 + 학술 검색 우선 (RISS·KCI·Google Scholar·PubMed)
- **사상체질 참고 문서 분리**: `prompts/references/sasang.txt` 신설 — 동의수세보원 67방(태양2/태음24/소양17/소음24)
- 임상진료지침 자동 인용 (사상체질 시 동의수세보원 + 임상진료지침 2개)
- 변증 자동 모드 가이드 (1단락 제한)
- 사상체질 ↔ 변증시치 상호 배타 (백엔드 + UI)
- AI 문체 억제 강화

### 이미지 프롬프트 v0.5
- **🔴 최우선 준수 섹션 신설**: 4대 원칙 (인체 묘사 정밀성 / 한국 의료 정체성 / 해부학 도해 형태 보존 / 색 변형 인접 색상 한정)
- 8K → web-resolution detail (900×900 모바일 대응)
- **한국 의료 정체성 시각 단서 7종**: 한약/침/뜸/부항/추나/진료실/의복 + 중국·일본 차단
- 공통 네거티브 강화: 관절 혼동, 디지트, 좌우대칭, 중국 TCM, 일본 kampo·shiatsu, 간체자·신자체
- **해부학 부위 disambiguation**: `anatomical_region` 필드 (leg/arm/foot/hand/back/head/torso/none)
- **단일 이미지 강제**: `generate as single standalone image, do not combine into grid/mosaic/collage`
- 의학 레퍼런스: WHO Standard Acupuncture Point Locations, Netter Atlas standard

### 이미지 결과 UI 통합 액션 바
- AI 선택 탭: Midjourney(기본) / ChatGPT / Gemini
- Midjourney: "5개 모두 복사" 1버튼 (가로 100%)
- ChatGPT/Gemini: 1~5번 개별 복사 버튼 5개 (`flex-wrap: nowrap`)
- ChatGPT/Gemini 복사 시 Midjourney 파라미터 자동 제거 + "Generate a single standalone image" 머리 삽입

### iframe 무한 재귀 수정
- 버그: `/`→`/app` 리다이렉트 + iframe `/` 로드 → 재귀 nesting → 사이드바 3개 표시
- 수정: `/dashboard` 라우트 신설(직접 서빙), iframe 초기 path `/dashboard`로 갱신

### AI 도우미 베타용 차단
- 결정: 에이전트 챗 시스템(`agent_router.py` + 8 prompt)은 베타 미노출
- 차단: 사이드바 "AI 도우미 (준비 중)" 비활성, 모바일 nav 비활성, `/chat` → `/dashboard` 리다이렉트
- 유지: 백엔드 인프라 그대로 — 베타 이후 자연어 라우팅 어시스턴트로 재구현 예정

---

## 2026-04-27

### 베타 런치 트랙 (B1~B4 Step 1)
- **B1 백업**: launchd 04:00 일일 (`scripts/backup.sh` + plist). openssl AES-256-CBC + Keychain
- **B2 모니터링**: `src/observability.py` (Sentry SDK + structlog + RequestLoggingMiddleware + PII 마스킹). `data/cligent.log` json line. `daily_report.py` 메트릭 섹션
- **B3 약관/방침/사업자정보**: `templates/legal/{terms,privacy,business}.html`. 자가 비번 재설정 `/forgot-password`. `_is_admin_clinic()` 헬퍼로 invite/reinvite 차단
- **B4 Step 1 랜딩**: `templates/landing.html` 8섹션. `/` 비로그인 시 랜딩, 로그인 시 /app
- 메모리: `project_beta_launch_track.md`

### 다양성 보강 v0.3 (142/142 테스트 통과)
- **포맷 6종**: `prompts/formats/` — information / case_study / qna / comparison / seasonal / lifestyle
- **훅 5종**: `src/hook_selector.py` — statistic / case / question / season / classic_quote
- **인용 풀**: `src/citation_provider.py` — RISS·KCI 동적 + 정적 고전 풀
- **패턴 엔진**: `exclude_intro`, `exclude_body_ids` 파라미터로 중복 방지
- **충돌 해결 10건**: hook-format 비호환 행렬, format-body 중복 배제, 참고자료 이중 출력, 계절 컨텍스트 주입, lifestyle 글자수 가드, case_study 독자 수준 자동업, qna FAQ 스키마 생략, comparison 광고모드 CTA 제한
- 신규: `src/format_selector.py`, `src/citation_provider.py`, `src/hook_selector.py`, `prompts/formats/*.txt`, 테스트 4개

---

## 2026-04-25

### 블로그 생성기 UX 개선
- 버그: `build_prompt_text()` `explanation_section=""` 누락 → `KeyError` 수정
- 1단계 시리즈 칩: "요즘 잘되는 키워드"(하드코딩) → "이어서 쓰면 좋을 시리즈 주제" (localStorage `cligent_series_topics`)
- 복원 배너 제거: `cligent_draft` localStorage 로직 전체 제거
- 3단계 결과 UI 통일: 글자수 + 안내 + 토큰정보 동일 스타일, 버튼 4개 `result-btn` 클래스
- 설정 > 콘텐츠 에이전트: 프롬프트 편집·글자 수 설정 제거, 생성 이력 섹션 추가

### 베타 모집 + 초대 발송 시스템 (완성)
- DB 테이블 `beta_applicants` (인덱스 2종)
- API: `POST /api/beta/apply` (IP 레이트 리밋: 5분/3회), `/join`, `/admin/applicants`, `/api/admin/applicants`, `/api/admin/invite-batch` (Semaphore 5)
- 이메일 (`src/plan_notify.py`):
  - E1 신청 확인 / E2 어드민 알림 / E3 초대 링크 / E4 72h 리마인더 / E5 clicked_at 자동
  - D3 `complete_onboarding()` → status='registered'
- 공통 헬퍼 `_send_smtp(to, subject, html_body) → bool` (fail-soft)
- 신규 ENV: `ADMIN_SECRET`, `ADMIN_CLINIC_ID`, `ADMIN_USER_ID`, `ADMIN_NOTIFY_EMAIL`, `BASE_URL`, SMTP 5종

---

## 2026-04-24

### blog.txt 품질 고도화 (CEO 리뷰 반영)
- 8개 섹션 신규/강화:
  - 5초 서론 훅: 통계형/공감형/반전 사실 강제
  - 체류시간 유도: 예고문·연결 문장
  - SEO: 키워드 6~8회, 수치는 실제 기관 데이터만
  - GEO 최적화: AI 검색 인용 구조화
  - 고유 임상 관점: 진료실 경험
  - 이미지 배치 마커: `[📷 이미지 삽입 제안: ...]` 2~3곳
  - AI 문체 억제: "먼저/다음으로/따라서" 반복 금지, "~에 대해 더 자세히 살펴보겠습니다." **1회라도 완전 금지**
  - 시리즈 주제 3개 필수
- `templates/index.html` SEO 라벨 6~8회, 쉼표 placeholder, "+" 버튼 제거
- 서버사이드 keyword 정규화: 쉼표 구분, 공백은 키워드 내부 보존
- inject 대상에서 `## 참고`, `## 미주`, `## 출처`, `## References` 제외

### 블로그 SEO 개선
- 키워드 삽입 횟수: 글자 수 비례 (1,500자→4~5회 … 2,500자↑→7~8회)
- 스마트블록 최적화: 네이버 연관 검색어를 소제목으로
- 이미지 마커 형식: `alt:` 권장 텍스트
- 저자 byline 삽입 (마무리 → byline → 참고 자료 순서)
- `extract_faq_schema()` 추가 — Q:/A: 파싱 → FAQPage JSON-LD (자체 웹사이트용, 네이버 미적용)
- 미적용 결정: AI disclosure(신뢰 저하), 카니발라이제이션 방지(C-Rank 역효과), BlogPosting JSON-LD(네이버 미지원)

### 이미지 프롬프트 규칙 추가
- 침 치료: 환자복 필수, 피부 노출 부위 침 삽입 (옷 위 침 금지). 네거티브 `needle through clothing`
- 해부학 도해: 흰색/투명 배경, 비율 보존 (uniform scale only, 1:1 고정)
- `_extract_image_markers()`: `[📷 이미지 삽입 제안: ...]`을 Stage 1 priority 힌트로

### UX 개선
- SEO 키워드 입력창 "+" 제거 — 자동 flush
- 대화 단계 스크롤: `scrollIntoView({ behavior: 'smooth', block: 'start' })` + 80ms 지연

---

## 2026-04-23

### T1+T6 블로그 생성기 업데이트

**데이터 개인정보 보호 (P0/P1)**
- Q&A 입력 영역마다 개인정보 경고 UI (의료법 제19조)
- `prompts/blog.txt` 환자 사례 생성 차단 강화
- `blog_history.py` 전면 재작성: `data/blog_stats.json`(영구) / `data/blog_texts.json`(30일 TTL)
- `purge_expired_texts()` — `lifespan()` 자동 실행
- 결과 화면 "30일 후 자동 삭제" 안내

**T1 프롬프트 복사 + T6 외부 AI 연동**
- `POST /build-prompt` — Claude API 호출 없이 프롬프트 조립만 (plan_guard 미적용)
- 재료 단계 T6 AI 바: 다른 AI로 프롬프트 복사 | Claude.ai | ChatGPT | Gemini
- `openInAI(platform)`: 복사 + AI 새 탭 + 토스트
- 온보딩 Step 2 전면 교체: T6 3단계 가이드
- `finishT6Onboarding()`: 배너 숨김 + `/blog` 이동
- Step 1에 T4 "대표 계정 토큰" **준비 중**

---

## 2026-04-22

### BYOAI 모델 + 온보딩 위자드
- 사용자가 본인 Anthropic API 키 직접 입력
- claude-sonnet-4-6 기준 1편 ≈ ₩7~14
- 온보딩 위자드 (`app.html` 모달): 로그인 직후 `api_key_configured=false`이면 자동
  - RBAC: `chief_director`만 / 그 외 → "원장님께 요청"
  - 4단계: AI 경험 → 시나리오 → 키 입력(실시간 검증) → 완료
  - `POST /api/settings/clinic/ai/validate` — 실제 Anthropic 호출
  - `POST /api/settings/clinic/ai/onboarding-start`
  - `first_blog_at` — 첫 블로그 완료 시 자동 기록
  - 대시보드 카드 "첫 블로그까지 소요: N분" 배지

### 결제 시스템 Phase 1 (커밋 aaf534a)

**플랜**: free(월 3편) / standard(무제한, 29,000원) / pro(무제한, 59,000원)

**신규 파일:**
- `src/plan_guard.py` — 블로그 한도 체크 (60s TTL 캐시, fail open)
  - 우선순위: `plan_expires_at` → `trial_expires_at` → 무료 월 3편
  - trial abuse 방어: `trial_expires_at` 재설정 코드 없음
- `src/usage_tracker.py` — 사용량 로깅 (실패 시 서비스 영향 없음)

**DB 변경**: `plans` + 시드 / `usage_logs` + 인덱스 / `subscriptions` 빈 셸 / `clinics` 컬럼 4종

**테스트**: `test_plan_guard.py`(10) + `test_usage_tracker.py`(3) = 13/13

**Python 3.9 호환**: `Optional[dict]` 사용 (`dict | None` 불가), lazy import 패치는 `db_manager.get_db` 경로

---

## 2026-04-21

### 이미지 결과 UI 업데이트
- `resultFooter` 바로가기 드롭다운: Midjourney / ChatGPT / Gemini / Ideogram / Leonardo AI
- 이미지 프롬프트 카드 링크 3종
- 시리즈 주제 "선택" 버튼 — localStorage 저장 → reset() 시 자동 입력

### 키워드 보강 로직
- `generate_blog_stream()`에서 주제(keyword)를 SEO 키워드에 자동 포함
- `_fix_keyword_counts()` inside_link 감지: rfind 방식

### 비밀번호 재설정 흐름
1. 설정 > 팀 관리 > 직원 → "비밀번호 재설정 링크 생성"
2. `POST /api/settings/staff/{id}/reinvite` → 72h 토큰
3. `/onboard?token=...` → 새 비밀번호
4. `complete_onboarding()` — 기존 UPDATE / 신규 INSERT

### 신규 API
- `GET/POST /api/settings/clinic/profile`
- `GET/POST /api/settings/clinic/ai` (Fernet 암호화)
- `GET/POST /api/settings/blog`
- `GET/POST /api/settings/blog/prompt`
- `POST /api/settings/staff/{id}/reinvite`

---

## 2026-04-20

### 이미지 프롬프트 2단계 파이프라인 전면 개선
- Stage 1: `image_analysis.txt` — 블로그 분석 → 장면 계획 JSON
- Stage 2: `image_generation.txt` — JSON + 스타일/톤 → 프롬프트 배열 JSON
- `image_prompt_generator.py` SSE 오케스트레이터
- 경혈 30개 (WHO 기준)
- 이미지 형식 6종 / 톤 6종

### 버그 수정
- `.env` 첫 줄 탭 문자 → `ANTHROPIC_API_KEY` 미인식 수정
- `load_dotenv(ROOT / ".env", override=True)` — 시스템 환경변수 덮어쓰기

---

## 2026-04-19

### 앱 쉘 구조 확정
- 진입: 로그인 → `/app` → `app.html` 쉘 → iframe `/dashboard`
- 사이드바는 `app.html`에만, iframe 페이지는 사이드바 숨김 (`if (window.self !== window.top)`)
- localStorage `cligent_sidebar`: '1'(접힘) / '0'(펼침)
- 모바일 (`max-width: 767px`): 사이드바 숨김, 하단 고정 `#mobile-nav` 4탭

### 사이드바 통일
- 기준 파일: `dashboard.html` canonical
- 토글 CSS: `.ios-toggle:checked ~ .ios-toggle-dot` (`+` 아님, `~`)
- 하단 구성: `role-badge` → `invite-btn` → `doLogout()`
- collapsed 숨김: `nav-label`, `sidebar-logo-text`, `sidebar-role`, `sidebar-invite-label`

---

## 2026-04-18

### 인증 시스템 완성
- JWT httpOnly 쿠키 (8h, SameSite=Lax)
- 5단계 RBAC: chief_director > director > manager > team_leader > team_member
- 초대 기반 온보딩, 슬롯 관리(clinic max_slots), SECRET_KEY 시작 시 검증

---

## 2026-04-16

### 블로그 생성기 추가 개선
- ClipboardItem API — HTML 형식 복사 (네이버 서식 유지)
- 이미지: 한의사 흰 가운, 현대적 클리닉 인테리어, 진료실 컴퓨터·모니터
- 경혈 위치: WHO 기준 ST36·LI4·PC6·SP6 등 9개 해부학적 위치 명시
- 참고 자료: 미주 URL 링크 (확실한 URL만), "(정확한 권호 확인 필요)" 제거
