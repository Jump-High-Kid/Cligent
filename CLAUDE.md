# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 세션 시작 시 자동 확인

세션 시작 시 `data/feedback_report.md` 파일이 존재하면 내용을 읽어 요약 보고한다.
파일이 없으면 무시한다. 확인 후 파일을 삭제하지 말 것 — 개발자가 수동으로 `data/feedback_ack.txt`를 갱신한다.

## 프로젝트 이름
**Cligent** (GitHub: https://github.com/Jump-High-Kid/Cligent)


## 프로젝트 개요

한의학(韓醫學) 보조 애플리케이션 **Cligent**. 한의사 임상 업무 지원을 목적으로 하는 의료 소프트웨어.

## 도메인 규칙

- **환자 데이터**: 개인정보보호법(PIPA) 및 의료법 준수 필수
- **한의학 용어**: 한글 + 한자 병기 (예: 변증(辨證), 기혈(氣血), 경락(經絡))
- **의학 정보**: 검증되지 않은 치료 효과는 사실로 제시 금지 — 항상 불확실성 명시
- **처방 데이터**: 약재명은 KCD 또는 표준 한의학 용어 사용

## 현재 구현 상태 (2026-04-30 기준)

### 사상체질 임상진료지침 1차 출처 격상 (2026-04-30 후반)

**`prompts/references/sasang.txt`** 보강 (93→177줄):
- **1차 출처 격상**: 사상체질의학회. (2022). 『사상체질병증 한의표준임상진료지침』. 한의약혁신기술개발사업단. 연구책임자 이준희. 정부 발간물.
- **2차 출처**: 이제마 동의수세보원 신축본(1901) — 처방 67방 원전
- **충돌 시 1차 우선** 명시 (LLM 사용 규칙에 박음)

**신규 섹션 5 — 권고사항 요약**:
- 5-1 KCD-8 12개 중분류 (소음인 U95.0~4 / 소양인 U96.0~4 / 태음인 U97.0~4 / 태양인 U98.0~2)
- 5-2 진단·평가 (R1, R2 — GPP/CTB)
- 5-3 공통 치료 (R3~R8 — GPP/CTB, 한약·침·뜸·식사·운동·성정)
- 5-4 특정 질환 (R9~R20):
  - **B/Moderate 2건**: R12 비만(태음조위탕), R16 당뇨(청폐사간탕+양약)
  - C/Low 10건: 뇌졸중 후유증, 비만 운동, 당뇨 식사, 고지혈증, 고혈압, 파킨슨, 불면 등
  - **A등급 0건** (지침에 A 권고 없음 — 블로그 인용 시 절대 표기 금지)
- 5-5 예방 (R21~R23 — GPP/CTB)
- 5-6 권고등급 의미

**신규 섹션 6 — 활용 규칙**:
- ✅ 권장: 권고등급 인용("B등급(중간 근거)"), KCD 코드 ICD 친화 표현
- ❌ 금지: A등급 임의 부여, KCD 임의 매칭, "효과 입증" 단정 표현
- 1편당 1차 출처 인용 **필수 1회 이상**

**옵시디언 위키 ingest 완료**:
- `jhzwiki/raw/사상체질병증임상진료지침_2022_사상체질의학회.pdf` (332p, 검색 친화 파일명)
- `jhzwiki/wiki/sources/사상체질병증_한의표준임상진료지침.md` (frontmatter + R1~R23 색인 + wikilink)
- `jhzwiki/wiki/concepts/사상체질병증_KCD8분류.md` (12개 중분류 KCD 매핑)
- `.index.md` 3→5페이지, `log.md` ingest 항목 추가


### 어드민 패널 P2 5종 완료 (2026-04-30)

**1. 신청자 라이프사이클 강화 (`/admin/applicants` 전면 재작성)**
- `beta_applicants` 9 컬럼 추가: `application_type`(beta/general), `marketing_consent`, `consented_terms_version`(`v1.0-2026-04-29`), `ip_address`, `user_agent`, `rejection_reason`, `admin_notes`, `admin_tags`, `expires_at`
- 신규 테이블 `applicant_emails`(applicant_id, email_type, sent_at, success, error_msg) — 모든 발송 결과 자동 ledger
- status enum 확장: pending / invited / registered / **rejected** / **expired**
- `_send_smtp(..., applicant_id, email_type)` 옵션 인자 → 자동 ledger
- `/api/beta/apply` 강화: IP/UA 자동 기록, 약관 동의 체크박스(필수: 개인정보 / 선택: 마케팅), `expires_at = now + 30일`
- `templates/join.html` 동의 UI + 안내 문구
- lifespan `_applicant_expiry_scheduler` 24h 주기 — 30일 미가입 자동 expired
- 어드민 API 5종: `GET /api/admin/applicants?type=&status=`(퍼널 stats) / `GET .../{id}/emails` / `PATCH .../{id}`(notes/tags) / `POST .../{id}/reject` / `POST .../{id}/resend`(apply_confirm/admin_notify/invite/reminder)
- UI: 6 퍼널 카드(클릭 필터), 유형 필터, 행 펼침 timeline, admin_notes/tags 인라인 편집(debounce 700ms), 거절 모달, 재발송 드롭다운

**2. 로그인 이력 PIPA 90일 (`/admin/login-history` + 사용자 본인)**
- 신규 테이블 `login_history`(user_id/email raw/clinic_id/ip/user_agent/success/failure_reason/created_at) + 인덱스 3종
- `auth_manager.authenticate_user` 시그니처 → `(user, failure_reason)` 튜플 (user_not_found / invalid_credentials / password_not_set / disabled)
- `record_login_attempt()` 헬퍼 — 모든 예외 흡수 fail-soft
- `/api/auth/login`에서 성공·실패 모두 IP/UA 추출 자동 기록
- lifespan `_login_history_purge_scheduler` 24h, 90일 초과 자동 삭제
- 어드민: `GET /api/admin/login-history?days=&success=&email=&ip=` — stats + **의심 IP 자동 감지**(1시간 내 동일 IP 5회 이상 실패)
- 사용자 본인: `GET /api/auth/login-history` (최근 90일 50건) → `templates/settings.html` 시스템 & 보안 > 보안 서브탭 인라인
- **SQLite native datetime 비교** 필수: `datetime(created_at) >= datetime('now', ?)` — 형식 차이(공백 vs T) 회피
- tests/test_auth.py 4건 시그니처 호환 업데이트(20/20 통과)

**3. 에러 로그 뷰어 (`/admin/errors`)**
- 기존 자산 활용 — `data/error_logs/{date}.jsonl`, `observability.py:_write_error_log` 자동 기록 (PII 마스킹 적용)
- API 3종: `GET /api/admin/errors/dates`(가용 일자+크기) / `GET .../summary?days=7`(일자별/top types/top paths) / `GET /api/admin/errors?date=&status=&error_type=&path_q=&limit=`(최대 1000)
- 손상된 jsonl 줄 skip, 잘못된 date → 400
- lifespan `_error_logs_purge_scheduler` 24h, 90일 초과 파일 unlink
- UI: 좌 사이드 일자 픽커 + 우 통계 3카드 + 미니 막대차트 + 필터 + 행 펼침 상세
- main.py에 `import re as _re` 모듈 레벨 추가

**4. 데일리 리포트 즉시 트리거 (`/admin/usage`)**
- 기존 `POST /api/admin/daily-report`(Bearer-only) → `_require_admin_or_session`로 확장 (CLI Bearer 호환)
- `_DATE_RE` 검증 추가
- `templates/admin_usage.html` 상단 카드: 날짜 선택(기본 오늘) + "즉시 생성" 버튼 + 결과 path 표시

**5. 전체 블로그 이력 (`/admin/blogs`)**
- `blog_history.save_blog_entry(..., clinic_id=None)` 옵션 인자 추가 → blog_stats.json에 `clinic_id` 필드 (기존 row=None 하위 호환)
- main.py `_stream_and_save` 호출 시 user clinic_id 전달
- `GET /api/admin/blogs?clinic_id=&q=&publish_status=&date_from=&date_to=&page=&per_page=` — blog_stats + clinics name + naver_checker pending_checks **통합**
- 발행 상태 4단계 종합: none / pending / found / missing (naver_url 우선, pending_checks 보조)
- UI: 5 status 카드(클릭 필터), 클리닉 select·기간·키워드 검색, Top 클리닉 카드, 페이지네이션

### 어드민 nav 통일 (8링크)
모든 어드민 페이지 헤더 nav: 어드민 / 신청자 / 한의원 / 사용량 / 피드백 / 로그인 이력 / 에러 / 블로그 / 설정

### lifespan 스케줄러 6종 (모두 24h 주기, 신규 3종 추가)
- 데일리 리포트(자정 직후 자동 생성) — 기존
- E4 베타 리마인더(72h 미클릭) — 기존
- 네이버 발행 확인 — 기존
- **신청자 30일 만료** — 신규
- **로그인 이력 90일 정리 (PIPA)** — 신규
- **에러 로그 90일 정리** — 신규

### 보안 헤더 (Caddyfile, 2026-04-29)
`/opt/homebrew/etc/Caddyfile` cligent.kr/cligent.co.kr 블록에 추가:
```
header {
    Strict-Transport-Security "max-age=300"
    X-Content-Type-Options "nosniff"
    Referrer-Policy "strict-origin-when-cross-origin"
    X-Frame-Options "SAMEORIGIN"
}
```
- includeSubDomains·preload 미사용 (점진 적용)
- 1주 후 max-age=86400, 1개월 후 31536000으로 단계 상향 예정

### OG 이미지 (1200×630, 2026-04-29)
- 위치: `static/og-image.png` (180KB)
- 디자인: emerald(#064e3b) 배경 + 흰 글씨, 흰 박스 + emerald C 글리프 + AI 스파클(favicon SVG 그대로 인라인) + Cligent 워드마크 168px, 카피 "원장님의 시간을 돌려드립니다." (시간 sage underline) + "Medical AI Agent, Cligent"
- 생성 방법: `/tmp/og.html` HTML+CSS → Chrome headless `--window-size=1200,630 --force-device-scale-factor=1 --virtual-time-budget=10000`

### 랜딩 리뉴얼 1·2단계 + SEO 인프라 (2026-04-29)

**1단계 — 카피·구조 (`templates/landing.html`):**
- 한의원장→**원장님** 통일, 베타 배지 "선착순 5명 무료" 제거
- Hero h1: "원장님의 **시간**을 돌려드립니다" (시간 단어만 accent), 서브카피 마케팅 톤
- Problem 섹션 마케팅·신환 확보 톤 (네이버 C-Rank, 마케팅 대행 200~1000만원, 도구 4~5개)
- Solution **3 STEP→4 STEP** (주제→옵션→AI 생성+학술 검색→이미지), "30초"→"1~2분"
- **BYOAI 섹션 → "왜 Cligent인가" 비교 섹션**: 7개 항목(한의학 이해도·블로그 프롬프트·**휴머나이저**·이미지·참고 문헌·이용량·운영·고도화)
- **로드맵 섹션 신설** (Trust 다음): 현재(블로그+이미지)·다음(유튜브)·이후(재고/문진/CRM)·비전
- 베타 섹션 "선착순 5명 무료" → "베타 테스터로 먼저 만나보세요"

**2단계 — 디자인:**
- **soft sage 보조 컬러** 토큰 신설(`--sage:#a8b5a0`, `--sage-soft:#eef1ea`, `--sage-tint:#f6f8f4`)
- 배경 아이보리(#faf9f5)→**pure white** 통일
- 타이포 위계 강화(h1 40~76px, 자간 -0.04em, "시간" accent에 sage 하이라이트 underline)
- 카드 elevation+hover(translateY -3px, shadow-md, sage border) — problem/trust/roadmap/FAQ
- 로드맵 "현재" 카드는 emerald 배경 그라데이션
- **section-label에 sage dash** 추가(`— SECTION` 형태)
- 히어로에 sage+emerald 라디얼 글로우 2개
- 베타 섹션 sage-tint 그라데이션 + 카드 shadow-md
- **비교표 강화**: Cligent 헤더 emerald 풀 배경 + 흰 글자, 셀에 ✓ 자동 prefix, 행 hover sage-tint
- 헤더 backdrop blur 12px, 로고 옆 **"Beta" 배지** 추가(sage-soft 배경)
- 로고 SVG: `font-size 26, x=4 y=27`, 스파클 `translate(23 8) scale(0.08)`, 색 **pure white** 통일
- 섹션 padding 64→96px, footer sage-tint 배경

**SEO 인프라 (`src/main.py` + `templates/landing.html`):**
- FastAPI 라우트 신설:
  - `GET /robots.txt` — 공개 페이지 허용, `/api/`·`/admin`·`/app`·`/dashboard`·`/blog`·`/chat`·`/settings`·`/onboard`·`/login`·`/forgot-password`·`/youtube`·`/help` 차단
  - `GET /sitemap.xml` — 공개 4개 URL(/, /terms, /privacy, /business), `lastmod` 자동 갱신
  - `from fastapi.responses import Response` 추가
- `landing.html` head 메타 보강:
  - `<link rel="canonical">`, `<meta name="keywords|author|robots">`
  - **Open Graph 풀세트**: title/description/type/url/site_name/locale/image(1200×630)/image:alt
  - **Twitter Card**: summary_large_image
  - **JSON-LD `@graph`**: Organization + WebSite + SoftwareApplication
- **Google Search Console 소유권 인증 완료** (토큰: `1W24HKtWNVkWebhUsc-IyUG6TjyeVVNm3WN1Tpwb8dg`)
- **Naver Search Advisor 소유권 인증 완료** (토큰: `4f52868ae171c9987fd900323d156bc39f74b4d3`)
- **남은 작업**: 사이트맵 제출(Search Console + Search Advisor), URL 색인 요청, OG 이미지(1200×630 PNG) 생성

**알려진 이슈 — 모바일 Chrome 미해결:**
- cligent.kr이 모바일 Chrome에서 안 열림 (데스크톱 Chrome / 모바일 Naver는 정상)
- Safe Browsing 차단 아님(데이터 없음=신규 미색인)
- 가설: HTTP/3(QUIC)이 ASUS 공유기 UDP 443 포트포워딩 미통과 → Caddy `Alt-Svc: h3` 광고하지만 Chrome 모바일이 fallback에 실패하는 케이스 의심
- 다른 폰 결과 + Caddyfile 확인 후, 임시 해결: Caddy 전역에 `protocols h1 h2` 또는 라우터에서 UDP 443 포트포워딩 추가

### 대시보드 실데이터 전환 (2026-04-29)
- `templates/dashboard.html` 환자 관리 목업(KPI 5장·차트·CRM 후속조치·교육영상) 전부 제거
- 새 구성: 인사말+"새 블로그 작성" CTA / 사용량 3카드(이번 달·누적·첫 블로그까지) / **최근 생성한 블로그 리스트** (10건 + "더 보기") / 다음 시리즈 주제 칩 / 최근 공지 1건
- 발행 상태 뱃지 4단계: `미등록`·`대기 중`(파랑)·`✓ 발행 확인됨`(녹색·클릭 시 네이버 글)·`! 누락`(앰버)
- 액션: 보기(`/api/blog/history/{id}/text` → 새 창 미리보기), 발행 확인 등록(`/api/blog/history/{id}/publish-check`)
- `GET /api/blog/publish-status` 신규 — `{by_id: {blog_stat_id: {status, found_url, started_at, check_count}}}` 일괄 반환
- 헤더 우측 아이콘(알림·설정·사람) 통일 — 모두 `w-9 h-9 rounded-full border-emerald-700`, 사람 아이콘은 `/settings#system`(시스템 & 보안 탭)으로 이동
- `settings.html`에 해시 딥링크 추가 — `location.hash`(`#system`) 읽고 매칭하는 tab-btn 자동 클릭. `app.html` `updateActive`도 hash 제거 후 매칭

### 관리자 패널 P1 (2026-04-29)
- **`_require_admin_or_session(request)`** 헬퍼 신설(`src/main.py`) — 세션(chief_director + ADMIN_CLINIC_ID) **또는** `ADMIN_SECRET` Bearer. CLI 스크립트 호환 유지
- 진입점: `/admin` 인덱스(5카드) + `app.html` 사이드바·모바일 드로어 "관리자" 메뉴(`is_admin`만)
- 기존 `/admin/settings`, `/admin/applicants` 페이지의 secret 모달·`sessionStorage.admin_secret`·Bearer 헤더 모두 제거 → 세션 쿠키만으로 진입
- **신규 페이지 3개:**
  - `/admin/clinics` — 클리닉 전체 목록 + 메타 수정 모달(plan_id/trial_expires_at/plan_expires_at). API: `GET /api/admin/clinics`, `PATCH /api/admin/clinic/{id}`
  - `/admin/usage` — 이번 달 블로그·누적·프롬프트 복사·오늘 에러 + Top 50 클리닉 랭킹. API: `GET /api/admin/usage`
  - `/admin/feedback` — 필터(전체/미확인/확인됨), NEW 뱃지, viewed_at 토글. API: `GET /api/admin/feedback`, `POST /api/admin/feedback/{id}/viewed`, `POST /api/admin/feedback/{id}/unview`
- DB 마이그: `feedback.viewed_at TEXT` 컬럼 ALTER

### 참고 문헌 검증 가능성 강화 (2026-04-29)
- **문제**: 사용자가 생성된 블로그의 인용을 역검색했을 때 안 잡힘 → 가상 논문 할루시네이션
- **수정 (4단)**:
  - **A. RAG 항상 호출** — `search_all_academic`이 정보·전문 모드에서만 → 모든 모드에서. 0건 반환 시 시스템 프롬프트에 "학술 논문 형식 인용 작성 금지" 주입
  - **B. `prompts/blog.txt` 재작성** — "절대 원칙: 가상 인용 생성 금지" 섹션 신설, "4~6개 항목" 강제 제거, RAG 자료만 인용 가능
  - **C. `build_rag_context_for_prompt`** 5대 규칙: 번호 그대로 / 메타데이터·URL 그대로 / 인용구는 초록만 / 가상 논문 금지 (PubMed URL은 PMID 직링크)
  - **D. `citation_provider.build_citation_block`** — "원전 / 가이드라인" + "추가 검색 링크 — 클릭하여 직접 확인하세요" 두 그룹 분리

### 공지사항 게시판 (2026-04-29 신설, 2026-04-29 정책 확립)
- DB: `announcements` / `announcement_reads` / `announcement_attachments` 3 테이블
- 라우트: `/announcements` (목록), `/announcements/{id}` (상세), `/announcements/new`·`/{id}/edit` (admin only)
- API: `GET /api/announcements`, `GET/POST /api/announcements/{id}`, `PATCH/DELETE`, `POST /api/announcements/{id}/read`, `GET /api/announcements/unread-count`, `POST /api/announcements/upload-image`
- 권한: `_require_announce_admin` — chief_director + ADMIN_CLINIC_ID
- 본문: marked.js + DOMPurify 클라이언트 markdown 렌더, 이미지 첨부 5MB(jpg/png/webp/gif), `static/uploads/announcements/`에 저장
- 카테고리 3종(업데이트/점검/일반), 상단 고정, 안 읽은 공지 뱃지(사이드바 숫자, 모바일 점)
- **공지 작성 정책**: 외부 사용자용 — 내부 구현 상세(파일명·버전·정책 수치) 제외, 사용자 가치 중심. memory `feedback_announcement_scope.md`

### 모바일 UX 정비 (2026-04-29)
- `/mobile` 라우트 + UA sniff 제거 → **단일 진입점 `/app` 반응형**으로 통일
- 햄버거(좌상단) ↔ API 아이콘(우상단). 둘 다 `top-4 w-9 h-9 text-[20px] border-emerald-700 rounded-full bg-white`
- 햄버거 클릭 시 우측 슬라이드 드로어 — 도움말 / YouTube / 팀원 초대 / 관리자(is_admin) / 로그아웃
- 하단 nav 4탭: 대시보드 / 블로그(`edit_square` 아이콘 — 공지의 `campaign`과 시각 분리) / 공지(미확인 빨간 점) / 설정. 컨테이너에 `w-full` 추가해 좌우 균등 배치
- API 키 배너: `확인` ✓ → `닫기` ✗(close 아이콘)으로 변경, 일일 닫기 정책(`localStorage.cligent_apikey_banner_dismissed`). 등록 여부 무관 표시(원장 테스트 편의)

### 랜딩 정비 (2026-04-29)
- `templates/landing.html` 가격 정보 완전 제거: 네비 "가격" 링크, `#pricing` 섹션(Free/Standard/Pro 3카드 + 베타 안내) 51줄, FAQ "Standard 29,000원" 구체 가격
- FAQ 답변 → "정식 가격은 베타 운영 결과에 따라 추후 공지"

---

## 이전 구현 상태 (2026-04-28 기준)

### RAG 학술 검색 통합 (2026-04-28)
- **신규 파일**: `src/academic_search.py` — 3소스(jkom·PubMed·Naver doc) 병렬 검색 + 24h 디스크 캐시
- **검색 파이프라인**: 정보형/전문 블로그 생성 시 자동 동작
  - jkom.org: POST `/articles/search_result.php` + `key=` form data, 브라우저 UA + Referer 필수, urllib.request 사용 (httpx로는 timeout)
  - PubMed E-utilities: esearch.fcgi + efetch.fcgi, 무료, 한국어→영어 매핑 50+ 용어 (`_KO_EN` dict: 요추, 침, 추나, 추간판 등)
  - Naver doc.json: 기존 Naver Client ID/Secret 재사용, 권한 활성화 필요 (개발자 콘솔 > 검색 > 전문자료)
- **캐시**: `data/academic_cache.json` (24h TTL), `_cache_get/_cache_set` 헬퍼
- **블로그 통합** (`src/blog_generator.py`):
  - 검색 결과를 `build_rag_context_for_prompt()`로 system_prompt 끝에 append
  - AI가 실제 논문 인용 포함한 참고 문헌 섹션 작성
  - `rag_results` 있으면 기존 동적 citation_block 스킵 (이중 인용 방지)
- **SSE status 이벤트**: "학술 자료를 검색하고 있습니다..." → "N건의 학술 자료를 찾았습니다."
- **이전 가짜 인용 문제 해결**: `citation_provider.py`의 RISS·KCI 검색 URL 자동 조립 인용은 정적 풀(동의수세보원 등)만 남기고 동적 검색 링크는 RAG 결과 있을 때 비활성
- **신규 의존성**: `beautifulsoup4==4.12.3`
- **블로그 잘림 수정**: `max_tokens` 4500 → 8000

### 네이버 발행 확인 + 자동 인링크 (2026-04-28)
- **신규 파일**: `src/naver_checker.py` — Naver Search API blog.json 폴링, 발행 확인 큐
- **데이터**: `data/pending_checks.json` 백그라운드 폴링 (60m → 120m×5 → 360m×4 → 720m, 7일 만료)
- **API**: `POST /api/blog/history/{entry_id}/publish-check`, `GET /api/blog/notifications`, `POST /api/blog/notifications/{id}/dismiss`
- **UI**: 블로그 결과 화면 "발행 확인 등록" 버튼 + 모달 (보통 1~24시간, 신규 블로그 1~3일 안내)
- **설정**: 네이버 블로그 아이디는 콘텐츠 에이전트 > 블로그 설정 (placeholder: hani2025)
- **어드민**: `templates/admin_settings.html` Naver Client ID/Secret 입력 페이지 (`data/app_settings.json`)
- **알림**: 검색 적중 시 이메일 (`plan_notify.py`) + 대시보드 배너

### 의료법 고지문 자동 삽입 (2026-04-28)
- `src/blog_generator.py`의 `_inject_legal_disclaimer()`: 의료법 56·57조, 시행령 23·24조 준수 문구를 byline 바로 위 자동 삽입 (`---\n작성:` 또는 `\n작성:` 패턴 매칭)

### 블로그 프롬프트 v0.4 (2026-04-28)
- **체류시간 예고문 다양화**: 정형 문구 ("많은 분들이 놓치는 핵심...") 그대로 인용 금지, 8가지 형태(구체정보·질문자극·오해환기·행동유도·사례예고·핵심압축·실용가치·비교안내) 참고용 제시
- **"참고 자료" → "참고 문헌" 통일**: `prompts/blog.txt` 섹션 제목·`src/citation_provider.py` 헤더·블로그 구조 7번 모두 일괄
- **참고 문헌 4~6개 + 학술 검색 출처 우선**: RISS·KCI·Google Scholar·PubMed 4개 동적 + 정적 1 = 5개 자동. config.yaml `providers: ["riss","kci","google_scholar","pubmed"]`
- **사상체질 참고 문서 분리**: `prompts/references/sasang.txt` 신설 — 동의수세보원 신축본 67방(태양2/태음24/소양17/소음24) + LLM 사용 규칙(체질변증·체질방 화이트리스트, 일반 변증·일반 처방명 금지). `_build_explanation_section()`이 사상체질 선택 시 자동 주입
- **임상진료지침 자동 인용**: 사상체질 선택 시 정적 인용 1개 → 동의수세보원 + 사상체질의학회 임상진료지침 2개 고정
- **변증 자동 모드 가이드**: explanation_types 미선택 + 일반/건강관심 reader_level → "한의학에서는 [증상]을 [변증]으로 분류" 1단락 제한. 한의학관심 모드는 기존 자유 출력
- **사상체질 ↔ 변증시치 상호 배타**: 백엔드(`_build_explanation_section`) + UI(`toggleExplChip`) 양방향 가드
- **AI 문체 억제 강화**: "예시 문구 그대로 인용 금지" 규칙 추가 — 모든 예문은 참고용, 매번 다른 표현

### 이미지 프롬프트 v0.5 (2026-04-28)
- **🔴 최우선 준수 섹션 신설**: `prompts/image_generation.txt` 최상단 4대 원칙 — 인체 묘사 정밀성 / 한국 의료 정체성 / 해부학 도해 형태 보존 / 색 변형 인접 색상 한정
- **8K resolution 제거**: photorealistic 접두어에서 `8K resolution` → `web-resolution detail` (900×900 모바일 대응)
- **한국 의료 정체성 시각 단서 7종**: 한약(한지 약봉지·전탕기), 침(스테인리스 트레이 멸균), 뜸(간접뜸 paper barrier), 부항(투명 플라스틱 펌프), 추나(Korean Chuna table), 진료실(현대 한의원), 의복(한국 standard)에 한국식 명시 + 중국·일본 차단
- **공통 네거티브 강화**: 관절 혼동(elbow on leg, knee on arm), 디지트(extra/missing/fused fingers, six fingers), 좌우대칭, 중국 TCM(red lanterns, ceramic herb jars), 일본 kampo·shiatsu, 간체자·신자체 텍스트
- **해부학 부위 disambiguation**: `image_analysis.txt` scene 객체에 `anatomical_region` 필드 (leg/arm/foot/hand/back/head/torso/none) — 경혈 코드별 자동 매핑. `image_generation.txt`가 이 필드를 읽어 "LEG anatomy with knee joint and patella — NOT arm, NOT elbow joint" 식으로 명시 박아넣기
- **단일 이미지 강제**: 모든 프롬프트 끝에 `generate as single standalone image, do not combine into grid/mosaic/collage` 자동 부착 (Nano Banana 2/ChatGPT 정책 변경 대응)
- **의학 레퍼런스 충실도**: 해부학 도해 한정 `WHO Standard Acupuncture Point Locations, Netter Atlas standard, no creative reinterpretation`
- **스타일별 후미 파라미터에 정확성 추가**: photorealistic `anatomically accurate, medical illustration accuracy`, 그 외 `correct human anatomy`

### 이미지 결과 UI 통합 액션 바 (2026-04-28)
- `templates/index.html` `imagePromptOutput` 위에 통합 컨트롤 바 신설
- AI 선택 탭: Midjourney(기본) / ChatGPT / Gemini
- **Midjourney 선택 시**: "5개 모두 복사 (Midjourney)" 1버튼 (가로 100%) — 그리드 출력 친화
- **ChatGPT/Gemini 선택 시**: 1번~5번 개별 복사 버튼 5개 (`flex-wrap: nowrap` 한 줄 강제) — 모자이크 합성 회피
- ChatGPT/Gemini 복사 시 자동으로 Midjourney 파라미터 제거 + "Generate a single standalone image" 머리에 삽입
- 토스트 알림 (1.8초)
- 기존 카드별 버튼은 폴백으로 유지

### iframe 무한 재귀 수정 (2026-04-28)
- **버그**: `/`→`/app` 리다이렉트 + `app.html` iframe이 `/` 로드 → 재귀 nesting → 사이드바 3개·온보딩 위자드 3개 표시
- **수정**: `src/main.py`에 `/dashboard` 라우트 신설(dashboard.html 직접 서빙, 리다이렉트 없음). `templates/app.html` 사이드바 데이터·모바일 nav·iframe 초기 path 모두 `/dashboard`로 갱신
- 사용자 직접 `/`/`/app` 진입 → app.html → iframe `/dashboard` 단일 로드

### AI 도우미 베타용 차단 (2026-04-28)
- **결정**: 에이전트 챗 시스템(`agent_router.py` + 8개 prompt)은 베타에 노출하지 않음. Cligent 정체성과 다른 챗 동작 회피
- **차단**: 사이드바 "AI 도우미 (준비 중)" 비활성, 모바일 nav 비활성, `/chat` → `/dashboard` 리다이렉트
- **유지**: 백엔드 인프라(`/api/agent/chat`, agent_router/middleware, 8 YAML+prompt) 그대로 — 베타 이후 자연어 라우팅 어시스턴트로 재구현 예정 (의도: 자연어 → Cligent 기능 페이지 이동, 화이트리스트 기반 Q&A, 범위 외 질문 회피)

---

## 이전 구현 상태 (2026-04-27 기준)

### 베타 런치 트랙 (B1~B4 Step 1 완료)
- **B1 백업**: launchd 04:00 일일 (`scripts/backup.sh` + `~/Library/LaunchAgents/kr.cligent.backup.plist`). openssl AES-256-CBC + Keychain 비번
- **B2 모니터링**: `src/observability.py` (Sentry SDK + structlog + RequestLoggingMiddleware + PII 마스킹). `data/cligent.log` json line. `daily_report.py`에 메트릭 섹션 추가
- **B3 약관/방침/사업자정보**: `templates/legal/{terms,privacy,business}.html` + `static/legal.css` + 라우트 3개. 자가 비번 재설정 `/forgot-password`. 운영 도구 `scripts/reset_password.py`. 베타 정책 `_is_admin_clinic()` 헬퍼로 invite/reinvite 차단 + UI(can_invite 필드)
- **B4 Step 1 랜딩**: `templates/landing.html` 8섹션. `/` 라우트가 비로그인 시 랜딩, 로그인 시 /app로 자동
- 자세한 진행: 메모리 `project_beta_launch_track.md` + `~/.gstack/projects/Jump-High-Kid-Cligent/ceo-plans/2026-04-27-beta-launch-track.md`

### 다양성 보강 v0.3 (2026-04-27 완료, 142/142 테스트 통과)
- **포맷 6종**: `prompts/formats/` — information / case_study / qna / comparison / seasonal / lifestyle
- **훅 5종**: `src/hook_selector.py` — statistic / case / question / season / classic_quote
- **인용 풀**: `src/citation_provider.py` — RISS·KCI 동적 링크 + 정적 고전 풀
- **패턴 엔진**: `src/pattern_selector.py` — `exclude_intro`, `exclude_body_ids` 파라미터로 중복 방지
- **충돌 해결 10건**: hook-format 비호환 행렬, format-body 중복 배제, 참고자료 이중 출력, 계절 컨텍스트 주입, lifestyle 글자수 가드, case_study 독자 수준 자동업, qna FAQ 스키마 생략, comparison 광고모드 CTA 제한
- **신규 파일**: `src/format_selector.py`, `src/citation_provider.py`, `src/hook_selector.py`, `prompts/formats/*.txt`, `tests/test_format_selector.py`, `tests/test_citation_provider.py`, `tests/test_hook_selector.py`, `tests/test_blog_format_integration.py`



### 폴더 구조
```
medical-assistant/
├── run.py                  # 서버 시작 (python3 run.py)
├── conftest.py             # pytest 경로 설정
├── config.yaml             # 노코드 커스터마이징
├── requirements.txt
├── .env                    # API 키 + SECRET_KEY (gitignore)
├── .env.example
├── scripts/
│   ├── create_clinic.py        # 운영 클리닉 생성 CLI
│   └── create_demo_account.py  # ★ 영상 촬영용 데모 계정 생성 (매 촬영 전 실행)
├── agents/
│   ├── dev/                # Claude Code 에이전트 정의 (.md)
│   └── runtime/            # ★ 배포용 에이전트 YAML 설정
│       ├── blog-agent.yaml
│       ├── crm-agent.yaml
│       ├── inventory-agent.yaml
│       ├── schedule-agent.yaml
│       ├── interview-form-agent.yaml
│       ├── legal-advisor-agent.yaml
│       ├── tax-advisor-agent.yaml
│       └── help-agent.yaml         # ★ 도움말 전용 에이전트 (Haiku)
├── prompts/                # 프롬프트 텍스트 파일
│   ├── blog.txt            # 블로그 생성 시스템 프롬프트
│   ├── blog_patterns.txt   # ★ 블로그 패턴 카탈로그 (서론 7+본론 8+결론 7+화제전환 6)
│   ├── questions.txt
│   ├── conversation.txt
│   ├── image_prompt.txt
│   ├── image_analysis.txt
│   ├── image_generation.txt
│   └── agents/             # ★ 에이전트별 시스템 프롬프트
│       ├── blog-agent.txt
│       ├── crm-agent.txt
│       ├── inventory-agent.txt
│       ├── schedule-agent.txt
│       ├── interview-form-agent.txt
│       ├── legal-advisor-agent.txt
│       ├── tax-advisor-agent.txt
│       └── help-agent.txt          # ★ 도움말 전용 프롬프트 (Cligent 범위 외 차단)
├── data/
│   ├── cligent.db          # SQLite (users, invites, clinics)
│   ├── rbac_permissions.json
│   ├── blog_history.json   # 패턴 히스토리 포함 (pattern_combos 키)
│   └── agent_log.jsonl     # 에이전트 활동 로그 (SHA-256 해시, PIPA 준수)
├── src/
│   ├── main.py             # FastAPI 앱 (전체 라우트)
│   ├── auth_manager.py     # JWT, bcrypt, 초대 토큰
│   ├── db_manager.py       # SQLite 초기화 + 커넥션
│   ├── module_manager.py   # RBAC 권한 관리
│   ├── settings_manager.py # 설정 위자드 데이터
│   ├── blog_generator.py   # 블로그 SSE 스트리밍 + 패턴 선택 통합
│   ├── blog_history.py     # 생성 이력 저장
│   ├── pattern_selector.py # ★ 5레이어 패턴 조합 선택 엔진
│   ├── conversation_flow.py
│   ├── image_prompt_generator.py
│   ├── agent_router.py     # ★ 키워드 기반 의도 분류 + Path Traversal 방어
│   ├── agent_middleware.py # ★ SHA-256 로깅, 비용 계산, 할루시네이션 감지
│   └── config_loader.py
├── static/
│   └── favicon.svg         # ★ Cligent 브랜드 파비콘 (C 글리프 + AI 스파클)
├── templates/
│   ├── app.html            # ★ 앱 쉘 — 사이드바 고정 + iframe 내비게이션
│   ├── chat.html           # ★ AI 도우미 채팅 (에이전트 칩바 + 채팅 UI)
│   ├── dashboard.html      # 메인 대시보드 (iframe 내 로드)
│   ├── dashboard_mobile.html
│   ├── help.html           # ★ 도움말 페이지 (Q&A 15개 + AI 도우미 + 키워드 검색)
│   ├── login.html          # 로그인 + 비밀번호 변경
│   ├── onboard.html        # 초대 링크 온보딩
│   ├── index.html          # 블로그 생성기 (iframe 내 로드)
│   ├── settings.html       # 설정 (iframe 내 로드)
│   └── settings_setup.html # RBAC 초기 설정 위자드
└── tests/
    ├── test_blog.py
    ├── test_auth.py        # 20개 유닛 테스트
    ├── test_agent_router.py    # ★ 12개 (라우팅, Path Traversal)
    ├── test_agent_middleware.py # ★ 4개 (SHA-256, 할루시네이션)
    ├── test_agent_api.py       # ★ 5개 (API 엔드포인트)
    ├── test_beta_apply.py      # ★ 5개 (베타 신청 + IP 레이트 리밋)
    └── test_invite_batch.py    # ★ 6개 (배치 초대 + 어드민 인증)
```

### 인증 시스템 (2026-04-18 완성)
- **JWT httpOnly 쿠키** (8h 유효, SameSite=Lax)
- **5단계 RBAC**: chief_director > director > manager > team_leader > team_member
- **초대 기반 온보딩**: 원장이 링크 생성 → 카톡/문자 전달 → 직원 비밀번호 설정
- **슬롯 관리**: clinic당 max_slots 제한
- **SECRET_KEY**: 서버 시작 시 검증, .env 필수

### 블로그 생성기 (완성)
- **4단계 플로우**: 주제 입력 → 글자 수 선택 → 대화형 질문 → SSE 스트리밍 생성
- **글자 수 선택**: 기본(2000자)/가벼운 글(1500자)/상세한 글(2500~3000자)/직접 입력(최대 9999자)
- **이미지 프롬프트**: 블로그 완성 후 5개 자동 생성
- **복사 기능**: 네이버 서식 유지 HTML 복사
- **베타 제한**: 블로그 생성 10건 / 프롬프트 복사 30건 (누적, plan_guard.py)
- **버튼 상태**: API 키 미등록+한도 소진 시 "프롬프트 복사" 활성, "블로그 생성" 비활성
- **개인정보 1회 모달**: 2단계 첫 진입 시 세션당 1회 표시 (sessionStorage)
- **피드백 바**: 페이지 상단 입력창 → POST /api/feedback → `data/feedback.jsonl` 저장 (개발자 전용 열람, 사용자 열람 불가)
  - 5건 누적 시 `data/feedback_report.md` 자동 생성 → 세션 시작 시 자동 보고

#### 커스터마이징 (config.yaml)
```yaml
flow:
  questions_enabled: true   # 질문 단계 on/off
  questions_count: 3        # 질문 개수
blog:
  min_chars: 1500
  max_chars: 2000
  tone: "전문적"
prompts:
  questions: "prompts/questions.txt"
  blog: "prompts/blog.txt"
```

#### 이미지 프롬프트 생성 기능 (2단계 파이프라인, 2026-04-20 전면 개선)
블로그 생성 완료 후 "이미지 프롬프트 생성" 버튼 → 옵션 패널 표시 → "생성하기" 클릭으로 5개 프롬프트 생성.

**아키텍처 (Option C — 구조화 JSON 출력)**
- Stage 1: `image_analysis.txt` — 블로그 분석 → 장면 계획 JSON (경혈 자동 선택, 카메라 앵글)
- Stage 2: `image_generation.txt` — JSON + 스타일/톤 → 이미지 프롬프트 배열 JSON
- `image_prompt_generator.py` — 2단계 파이프라인 오케스트레이터 (SSE)

**경혈 테이블: 30개 (WHO 기준)**
LU7, LI4, LI11, ST25, ST36, ST40, ST44, SP6, SP10, HT7, SI3, BL17, BL23, BL40, BL60, KD3, KD6, PC6, TE5, GB20, GB21, GB34, LV3, GV4, GV14, GV20, CV4, CV6, CV12, CV17

**이미지 형식 옵션 (6종)**
사실적(photorealistic) / 애니메이션(anime) / 카툰(cartoon) / 일러스트(illustration) / 수채화(watercolor) / 3D 렌더(3d_render)

**이미지 톤 옵션 (6종)**
따뜻한(warm) / 클린 화이트(cool_white) / 소프트(soft) / 에디토리얼(editorial) / 미니멀(minimal) / 내추럴(natural)

**API 파라미터**: `POST /generate-image-prompts` — `{ keyword, blog_content, style, tone }`

**블로그 생성 결과 하단 UI (2026-04-21 업데이트)**
- `resultFooter` 바로가기 드롭다운: Midjourney / ChatGPT / Gemini / Ideogram / Leonardo AI
- 이미지 프롬프트 카드 링크: Midjourney / ChatGPT / Gemini
- 시리즈 주제 "선택" 버튼: 클릭 시 화면 이동 없이 localStorage 저장 → reset() 시 자동 입력

**키워드 보강 로직 개선 (2026-04-21)**
- `generate_blog_stream()`에서 주제(keyword)를 SEO 키워드에 자동 포함 (seo_keywords=[] 대응)
- `_fix_keyword_counts()` inside_link 감지: rfind 방식으로 교체

**버그 수정 (2026-04-20)**
- `.env` 첫 줄 탭 문자 → `ANTHROPIC_API_KEY` 미인식 수정
- `src/main.py`: `load_dotenv(ROOT / ".env", override=True)` — 시스템 환경변수 덮어쓰기

**의료 윤리 준수**
- 환자 얼굴 정면 클로즈업 금지 (측면·후면·손 허용)
- 처방전·의료 기록 노출 금지
- 특정 약재 치료 효과 암시 금지

#### T1+T6 블로그 생성기 업데이트 (2026-04-23 완료)

**데이터 개인정보 보호 (P0/P1)**
- Q&A 입력 영역마다 개인정보 경고 UI (의료법 제19조) 표시
- `prompts/blog.txt` — 환자 사례 생성 차단 강화
- `blog_history.py` 전면 재작성: `data/blog_stats.json`(영구) / `data/blog_texts.json`(30일 TTL)
- `purge_expired_texts()` — `lifespan()` 서버 시작 훅에서 자동 실행
- 결과 화면 "30일 후 자동 삭제" 안내 + 데이터 보존 안내 UI

**T1 프롬프트 복사 + T6 외부 AI 연동**
- `POST /build-prompt` — Claude API 호출 없이 프롬프트 조립만 반환 (plan_guard 미적용)
- 재료 단계 T6 AI 바: `다른 AI로 프롬프트 복사 | Claude.ai | ChatGPT | Gemini` (복사만 버튼·드롭다운 제거)
- `openInAI(platform)`: 프롬프트 복사 + AI 새 탭 열기 + 토스트 안내
- 온보딩 Step 2 전면 교체: API 키 안내 → T6 3단계 가이드
- `finishT6Onboarding()`: T6 완료 시 배너 숨김 + `/blog` 이동
- Step 1에 T4 "대표 계정 토큰" **준비 중** 옵션 추가

#### 블로그 SEO 개선 (2026-04-24 완료)
- **`prompts/blog.txt`** 네이버 SEO 최적화:
  - 키워드 삽입 횟수: 글자 수 비례 (1,500자→4~5회 … 2,500자↑→7~8회)
  - 스마트블록 최적화 섹션: 네이버 연관 검색어를 소제목으로 활용 지시
  - 이미지 마커 형식: `alt:` 권장 텍스트 필드 추가
  - 저자 byline 삽입 지시 (글 구조 6번): 마무리 → byline → 참고 자료 순서
- **`src/blog_generator.py`**: `extract_faq_schema()` 추가 — Q:/A: 파싱 → FAQPage JSON-LD (자체 웹사이트용, 네이버 블로그 미적용)
- **`templates/index.html`**: FAQ 스키마 복사 버튼 주석 처리 (네이버 블로그 script 삽입 불가)
- **미적용 결정**: AI disclosure(독자 신뢰 저하), 카니발라이제이션 방지(C-Rank 역효과), BlogPosting JSON-LD(네이버 미지원)

#### 블로그 생성기 추가 개선사항 (2026-04-16)
- **복사 기능**: ClipboardItem API — HTML 형식 복사로 네이버 붙여넣기 시 서식(굵기·제목) 유지, 폴백 시 마크다운 기호 제거
- **이미지 프롬프트**: 한의사 흰 가운, 현대적 클리닉 인테리어(한옥 배경 제거), 상담실·진료실에 컴퓨터·모니터 배치
- **경혈 위치**: WHO Standard Acupuncture Point Locations 기준 ST36·LI4·PC6·SP6 등 9개 주요 경혈 해부학적 위치 명시
- **참고 자료**: 미주 URL 링크 추가 (확실한 URL만), "(정확한 권호 확인 필요)" 문구 제거 → 불확실 시 공란

#### blog.txt 품질 고도화 (2026-04-24 완료 — CEO 리뷰 반영)
- **`prompts/blog.txt`** 전면 재작성: 8개 섹션 신규/강화
  - 5초 서론 훅 (CRITICAL): 통계형/공감형/반전 사실 형식 강제
  - 체류시간 유도: 예고문·연결 문장
  - SEO: 키워드 **6~8회** 삽입, 수치는 실제 기관 데이터만 (근거 없는 숫자 금지)
  - GEO 최적화: AI 검색 인용 구조화 형식
  - 고유한 임상 관점: 진료실 경험 반영
  - 이미지 배치 마커: `[📷 이미지 삽입 제안: ...]` 2~3곳
  - AI 문체 억제: "먼저/다음으로/따라서" 반복 금지, "~에 대해 더 자세히 살펴보겠습니다." **1회라도 완전 금지**
  - 시리즈 주제 3개 필수 (결론 7번째 항목)
- **`templates/index.html`** — SEO 라벨 `6~8회`, 쉼표 구분 placeholder 업데이트, "+" 추가 버튼 제거 (생성 시 자동 flush)
- **`src/main.py`** — 서버사이드 keyword 정규화: 쉼표 구분, 공백은 키워드 내부 보존
- **`src/blog_generator.py`** — inject 대상에서 `## 참고`, `## 미주`, `## 출처`, `## References` 제외

#### 이미지 프롬프트 규칙 추가 (2026-04-24)
- **`prompts/image_generation.txt`**:
  - 침 치료 장면: 환자복 필수, 피부 노출 부위 침 삽입 (옷 위 침 금지), 네거티브에 `needle through clothing` 등 추가
  - 해부학 도해 장면: 흰색/투명 배경, 비율 보존 (uniform scale only, 1:1 고정)
- **`src/image_prompt_generator.py`** — `_extract_image_markers()` 신규: `[📷 이미지 삽입 제안: ...]` 마커를 Stage 1 분석에 priority 힌트로 전달

#### 블로그 생성기 UX 개선 (2026-04-24)
- SEO 키워드 입력창 "+" 버튼 제거 — 생성/복사 클릭 시 자동 flush
- 대화 단계 스크롤 수정: `scrollIntoView({ behavior: 'smooth', block: 'start' })` + 80ms 지연 (간헐적 미작동 해결)

#### 블로그 생성기 UX 개선 (2026-04-25)
- **버그 수정**: `build_prompt_text()` `explanation_section=""` 누락 → `KeyError: 'explanation_section'` 수정
- **1단계 시리즈 칩**: "요즘 잘되는 키워드"(하드코딩) → "이어서 쓰면 좋을 시리즈 주제" (localStorage `cligent_series_topics` 기반, 없으면 섹션 숨김)
- **복원 배너 제거**: `cligent_draft` localStorage 저장·복원 로직 전체 제거
- **3단계 결과 UI 통일**: 글자 수(중립 표시) + 안내문구 + 토큰정보 동일 스타일, 버튼 4개 `result-btn` 클래스로 높이·스타일 통일
- **설정 > 콘텐츠 에이전트**: 블로그 프롬프트 편집·글자 수 설정 제거, 생성 이력 섹션 추가 (`GET /api/blog/history`, `GET /api/blog/history/{id}/text`)

#### 개발 환경 참고
- `~/Library/LaunchAgents/kr.cligent.app.plist` — launchd KeepAlive 서비스, 재부팅 후 uvicorn 자동 재시작
- 개발 중 포트 충돌 시: `launchctl unload ~/Library/LaunchAgents/kr.cligent.app.plist`

#### 실행 방법
```bash
python3 run.py        # 서버 시작 → http://localhost:8000
python3 -m pytest tests/ -v   # 테스트 실행
```

#### BYOAI 모델 + 온보딩 위자드 (2026-04-22 완료)
- 사용자가 본인 Anthropic API 키 직접 입력
- claude-sonnet-4-6 기준 글 1편 ≈ ₩7~14
- **온보딩 위자드** (`app.html` 모달): 로그인 직후 `api_key_configured=false`이면 자동 오픈
  - RBAC: `chief_director`만 설정 가능 / 그 외 → "원장님께 요청" 화면
  - 4단계: AI 경험 선택 → 시나리오 안내 → 키 입력(실시간 검증) → 완료
  - `POST /api/settings/clinic/ai/validate` — 실제 Anthropic API 호출로 키 유효성 검증
  - `POST /api/settings/clinic/ai/onboarding-start` — `onboarding_started_at` COALESCE 기록
  - `first_blog_at` — 첫 블로그 완료 시 자동 기록 (`_stream_and_save`에서)
  - 대시보드 Blog Generator 카드에 "첫 블로그까지 소요: N분" 배지 표시

### 앱 쉘 구조 (2026-04-19 확정)

**진입 흐름**: 로그인 → `/app` → `app.html` 쉘 로드 → iframe에 `/` (대시보드) 로드

- `GET /app` → `templates/app.html` (인증 필수)
- 사이드바는 `app.html`에만 존재, iframe 콘텐츠 페이지는 사이드바 숨김
- 각 페이지: `if (window.self !== window.top)` 감지 → `#sidebar` 숨김, 마진 0
- 로그인 후 리다이렉트: iframe 안→`/`, 직접 접속→`/app`
- localStorage `cligent_sidebar` = `'1'`(접힘) / `'0'`(펼침) — 새로고침 유지
- **모바일 (`max-width: 767px`)**: 사이드바 숨김, 하단 고정 네비게이션 `#mobile-nav` 표시
  - 4개 버튼: 대시보드 / 블로그 / AI도우미 / 설정
  - iframe 높이: `calc(100dvh - 64px)` (하단 바 64px 확보)
  - `mobileNav(path)` — iframe src + history 업데이트 + `updateActive()` 동기화

**사이드바 메뉴 (정식 아이콘)**:

| 메뉴 | Material Symbol | 경로 | 상태 |
|---|---|---|---|
| 대시보드 | `dashboard` | `/` | 완성 |
| 블로그 생성기 | `article` | `/blog` | 완성 |
| AI 도우미 | `chat` | `/chat` | 완성 |
| 재고 관리 | `inventory_2` | `#` | 미구현 (Coming Soon) |
| 스케줄 관리 | `calendar_today` | `#` | 미구현 (Coming Soon) |
| 고객 관리 | `group` | `#` | 미구현 (Coming Soon) |
| 설정 | `settings` | `/settings` | 완성 |
| 도움말 | `help_outline` | `/help` | 완성 |

### 설정 페이지 구조 (2026-04-21 업데이트)

`templates/settings.html` — 6개 탭 구성:

| 탭 | 설명 | 상태 |
|---|---|---|
| 팀 & 권한 관리 | 직원 목록 + 모듈 권한 토글 + 초대/재초대 | 완성 |
| 콘텐츠 에이전트 | 블로그 설정 (질문/글자수/톤/프롬프트) | 완성 |
| 스케줄 관리 | 직원 근무 스케줄 설정 | 향후 구현 |
| 재고 관리 | 약재·물품 재고 설정 | 향후 구현 |
| 문헌 정리 | 한의학 문헌 수집·분류 설정 | 향후 구현 |
| 시스템 & 보안 | 서브탭 5개 — 한의원 프로필·AI 설정 완성, 나머지 준비 중 | 부분 완성 |

**시스템 & 보안 서브탭 (2026-04-22 기준):**
- 한의원 프로필 ✅ — 이름/전화/주소/진료과목/진료시간/원장소개 (chief_director 저장)
- AI 설정 ✅ — API 키 Fernet 암호화, 모델 선택 (Haiku/Sonnet/Opus), 월 예산 (chief_director)
- 플랜 & 사용량 — Phase 2 CTA UI 구현 예정 (DB 기반 Phase 1 완료)
- 보안 — 준비 중
- 데이터 관리 — 준비 중

**비밀번호 재설정 흐름 (2026-04-21):**
1. 설정 > 팀 관리 > 직원 선택 → "비밀번호 재설정 링크 생성" 클릭
2. `POST /api/settings/staff/{id}/reinvite` → 72h 토큰 반환
3. 직원이 `/onboard?token=...` 접속 → 새 비밀번호 설정
4. `complete_onboarding()` — 기존 사용자면 UPDATE, 신규면 INSERT

**새로 추가된 API (2026-04-21):**
- `GET/POST /api/settings/clinic/profile` — 한의원 프로필
- `GET/POST /api/settings/clinic/ai` — AI 설정 (Fernet 암호화)
- `GET/POST /api/settings/blog` — 블로그 설정 (config.yaml 직접 수정)
- `GET/POST /api/settings/blog/prompt` — 블로그 프롬프트 파일
- `POST /api/settings/staff/{id}/reinvite` — 비밀번호 재설정 링크

**모듈 권한 토글 항목 (팀 & 권한 관리 탭 우측 패널):**
- 에이전트 모듈: 콘텐츠 에이전트 / 스케줄 관리 / 재고 관리 / 문헌 정리
- 팀 관리: 팀 & 권한 관리
- 시스템 & 보안: 한의원 프로필 / AI 설정 / 플랜 & 사용량 / 보안 / 데이터 관리 (비활성, 대표 원장 고정)

## 디자인 시스템 원칙 (2026-04-19 확정)

### 핵심 규칙: 디자인 변경 시 전체 일관성 유지
대시보드 또는 사이드 패널의 디자인(폰트·색·이미지·아이콘·레이아웃 등)이 변경되면,
**모든 하위 페이지(설정, 블로그 생성기 등)에 동일하게 반영**해야 한다.

### 현재 확정된 디자인 토큰

| 항목 | 값 | 적용 위치 |
|---|---|---|
| 사이드바 배경 | `bg-stone-100` | 모든 페이지 사이드바 |
| 활성 메뉴 | `bg-emerald-900 text-white rounded-xl` | 모든 페이지 사이드바 |
| 비활성 메뉴 텍스트 | `text-stone-600` | 모든 페이지 사이드바 |
| 비활성 메뉴 호버 | `hover:bg-stone-200` | 모든 페이지 사이드바 |
| 아이콘 스타일 | `wght 300, FILL 0, GRAD 0, opsz 24` | 모든 페이지 |
| 폰트 | Pretendard (본문), Manrope (헤드라인) | 모든 페이지 |
| 주색 | `emerald-900` (#064e3b) | 강조, 버튼, 활성 상태 |

### 사이드바 공통 동작
- 접기/펴기 토글 (☰ 버튼) — 아이콘 전용(72px) ↔ 전체(288px)
- 상태는 `localStorage('cligent_sidebar')`에 저장 → 페이지 이동 시 유지

### 새 페이지 추가 시 체크리스트
- [ ] `app.html` 사이드바 메뉴에 항목 추가 (`data-path` 속성으로 경로 지정)
- [ ] FastAPI에 라우트 추가 (`src/main.py`)
- [ ] 페이지 HTML에 iframe 감지 코드 추가:
  ```js
  if (window.self !== window.top) {
    document.getElementById('sidebar').style.display = 'none';
    document.getElementById('main-content').style.marginLeft = '0';
  }
  ```
- [ ] 폰트: Pretendard (`cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9`)
- [ ] Material Symbols: `wght,FILL@100..700,0..1` range 파라미터 사용

### 설정 모듈 권한 동작 규칙
- `chief_director` / `director` 선택 시 → 모든 토글 `disabled=true` + "항상 접근" 메시지
- `team_member` 이하 선택 시 → 토글 자유롭게 ON/OFF + 변경 즉시 자동저장 (`POST /api/settings/staff/modules`)

### 사이드바 통일 원칙 (2026-04-19 확정)
- **기준 파일**: `dashboard.html` 사이드바가 canonical(정본)
- 사이드바 수정 시 dashboard.html → settings.html → index.html 순으로 동일하게 적용
- 토글 CSS: `.ios-toggle:checked ~ .ios-toggle-dot` (`+` 아님, `~` 사용)
- 하단 구성: `role-badge` → `invite-btn`(director 이상만 표시) → `doLogout()` 순서
- collapsed 숨김 클래스: `nav-label`, `sidebar-logo-text`, `sidebar-role`, `sidebar-invite-label`

### 멀티 AI 모델 지원 (Phase 1 완료, Phase 2+ 미구현)
- **Phase 1** ✅ — Claude API 키 저장(Fernet 암호화), 온보딩 위자드, 기본 모델 선택, 월 예산 설정
- **Phase 2** — 블로그 생성기에서 DB 저장 키 사용 (현재 .env 키 우선): 선택 모델 API 분기
- **Phase 3** — 멀티모델 비교 패널 / Gemini·ChatGPT 멀티 제공자 지원

### 베타 모집 + 초대 발송 시스템 (2026-04-25 완성)

**신규 DB 테이블:**
- `beta_applicants`: id, name, clinic_name, phone, email, note, applied_at, invited_at, clicked_at, invite_token UNIQUE, status(pending/invited/registered)
- 인덱스: `idx_beta_applicants_status`, `idx_beta_applicants_email`

**신규 API:**
- `POST /api/beta/apply` — 공개 베타 신청 (IP 레이트 리밋: 5분/3회)
- `GET /join` → `templates/join.html` (모바일 신청 폼)
- `GET /admin/applicants` → `templates/admin_applicants.html` (Bearer auth)
- `GET /api/admin/applicants` — 목록 + 통계 JSON (Bearer auth)
- `POST /api/admin/invite-batch` — 일괄 초대 발송 (Semaphore 5 병렬)

**이메일 플로우 (`src/plan_notify.py`):**
- E1 `send_beta_apply_confirm()` — 신청 확인 메일
- E2 `send_beta_admin_notify()` — 어드민 알림 (ADMIN_NOTIFY_EMAIL)
- E3 `send_beta_invite_email()` — 초대 링크 발송
- E4 `send_beta_reminder()` — 72시간 리마인더 (lifespan 스케줄러)
- E5 `verify_invite()` → `clicked_at` 자동 기록
- D3 `complete_onboarding()` → `status='registered'` 전환

**공통 헬퍼:** `_send_smtp(to, subject, html_body) → bool` (fail-soft, 미설정 시 로그만)

**신규 환경 변수:**
- `ADMIN_SECRET` — Bearer 토큰 (어드민 엔드포인트)
- `ADMIN_CLINIC_ID`, `ADMIN_USER_ID` — 시드 어드민 계정
- `ADMIN_NOTIFY_EMAIL` — 신청 알림 수신 이메일
- `BASE_URL` — 초대 링크 도메인 (https://cligent.kr)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_FROM` — Gmail SMTP

### 결제 시스템 Phase 1 (2026-04-22 완료 — 커밋 aaf534a)

**플랜 구조**: free(월 3편) / standard(무제한, 29,000원) / pro(무제한, 59,000원)

**신규 파일:**
- `src/plan_guard.py` — 블로그 한도 체크 (60s TTL 캐시, fail open)
  - 체크 우선순위: `plan_expires_at` → `trial_expires_at` → 무료 월 3편
  - trial abuse 방어: `trial_expires_at` 재설정 코드 없음
- `src/usage_tracker.py` — 사용량 로깅 (실패 시 서비스 영향 없음)

**DB 변경 (`db_manager.py`):**
- `plans` 테이블 + 시드 데이터
- `usage_logs` 테이블 + 인덱스 `idx_usage_logs_clinic_month`
- `subscriptions` 테이블 (Phase 3용 빈 셸)
- `clinics` 컬럼: `plan_id`, `plan_expires_at`, `trial_expires_at`, `payment_status`

**테스트:** `tests/test_plan_guard.py`(10개) + `tests/test_usage_tracker.py`(3개) = 13/13 통과

**Python 3.9 호환 주의:**
- `Optional[dict]` 사용 (3.10+ `dict | None` 불가)
- lazy import `get_db` 패치는 `db_manager.get_db` 경로로

## 기술 스택

- **백엔드**: Python 3.9 + FastAPI 0.115
- **AI**: Anthropic SDK (claude-sonnet-4-6) — SSE 스트리밍
- **프론트엔드**: Vanilla JS + HTML (fetch + ReadableStream)
- **설정**: config.yaml + prompts/ 폴더 (노코드 커스터마이징)
- **테스트**: pytest + FastAPI TestClient + unittest.mock

## 개발 시작 시 확인 사항

1. `.env` 파일에 `ANTHROPIC_API_KEY` 설정 (`.env.example` 참고)
2. `python3 -m pip install -r requirements.txt`
3. `python3 run.py` 로 서버 시작

## 프로덕션 배포 체크리스트

베타/운영 서버 배포 시 아래 항목을 반드시 확인한다.

### 필수 환경 변수
- [ ] `ENV=prod` — 미설정 시 기본값 "dev"로 동작. dev 모드에서는 서버 시작 시 `seed_demo_clinic()`이 실행되어 trial_expires_at 없는 데모 클리닉이 생성됨.
- [ ] `SECRET_KEY` — 미설정 시 서버 시작 실패 (의도적 fast-fail)
- [ ] `ANTHROPIC_API_KEY` — 미설정 시 블로그 생성 실패
- [ ] `ADMIN_SECRET` — 미설정 시 `/api/admin/clinic` 엔드포인트 비활성화 (403 반환). 베타 clinic 생성을 위해 설정 필요.
- [ ] `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` — 미설정 시 80% 알림 이메일 비활성화 (로그만 남김, 서비스에는 영향 없음)

### 신규 한의원 생성 (베타 참가자 등록)
trial_expires_at(14일)은 아래 두 방법 중 하나로 설정한다. `seed_demo_clinic()` 또는 직접 DB INSERT는 사용하지 말 것.

**방법 1 — CLI 스크립트** (권장):
```bash
python3 scripts/create_clinic.py --name "강남 한의원" --slots 5
```

**방법 2 — Admin API**:
```bash
curl -X POST http://localhost:8000/api/admin/clinic \
  -H "Authorization: Bearer <ADMIN_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"name": "강남 한의원", "max_slots": 5}'
```

### 플랜 우선순위 로직 (수정 시 주의)
`plan_guard.resolve_effective_plan()` 함수가 3곳에서 공유됨:
- `plan_guard.check_blog_limit()` — 블로그 생성 차단
- `plan_notify._notify_worker()` — 80% 알림 (trial/paid는 skip)
- `main.get_plan_usage()` — 설정 탭 사용량 표시

플랜 우선순위 로직 변경 시 이 함수 하나만 수정하면 됨.

## 인프라 현황 (2026-04-22 기준)

- **도메인**: cligent.kr, cligent.co.kr (가비아 DNS A 레코드 → 61.76.131.91)
- **서버**: 맥북 로컬 (192.168.50.132)
- **공유기**: ASUS RT-ACRH13 — 포트 80/443 → 192.168.50.132 포트포워딩 완료
- **리버스 프록시**: Caddy 2.11.2 (`/opt/homebrew/etc/Caddyfile`)
- **SSL**: Let's Encrypt 자동 발급 (Caddy 관리)
- **접속 URL**: https://cligent.kr (개통 완료)

### Caddy 관리 명령
```bash
brew services restart caddy   # 재시작
brew services info caddy      # 상태 확인
cat /opt/homebrew/etc/Caddyfile  # 설정 확인
```

## 가격·이미지 구조 v7 결정 (2026-04-30 — CEO 모드 토론)

상세는 메모리 참조: `~/.claude/projects/-Users-jhzmac/memory/project_cligent_pricing_v7.md` + `project_image_strategy.md`

### 핵심 결정

- **BYOAI 위자드 베타 비활성** (M3+ Lite로 단계 재도입). 베타는 All-in 정액제 단일.
- **가격**: Standard 14.9만(30건) / Pro 27.9만(80건+종량제) — 쏙AI(19.9만/90건) 대비 위·아래 협공
- **3개월 코호트**: 5인 무료 → 25인 1만원 → 50인 15% 할인 → 정식 M3+
- **이미지 모델**: gpt-image-2 단일 (mini는 의료 일러스트 품질 미달)
  - Standard 1024×1024 medium ($0.053/장)
  - Pro 1536×1024 high ($0.165/장)
- **Pro 출시 조건부**: 해부학 DB Phase 2 (100 부위) + 평균 재생성 1.5회 이하 + 만족도 80%
- **재생성·수정 정책**: Standard = 재생성 1회 + 수정 2회 무료 / Pro = 재생성 2회 + 수정 4회 무료. 초과 시 종량제 (베타 후 단가 산정).
- **Hybrid AI 모델**: 글 본문 Sonnet 4.6 / 메타(제목·태그·요약) Haiku 4.5 (즉시 도입)
- **edit endpoint 우선** 워크플로 (재생성 대신 부분 수정으로 세션 총비용 35% ↓)

### 해부학·경혈 DB가 사업 성패 lever

- 비용 1/3 절감 + Pro 가격 정당성 + visual moat (다른 SaaS 흉내 못 함)
- 자료 출처: Servier Medical Art (CC-BY 3.0), BodyParts3D, Wikimedia, WHO 표준 경혈
- Phase 1 (M0~M2) 30 부위 + 240 경혈 좌표 = **원장님 도메인 작업, critical path**

### 1주 인프라 작업 (사용자 OK 신호 대기)

| Day | 작업 |
|-----|------|
| 1 | 관리자 OpenAI API 키 등록 시스템 |
| 2~3 | image2 generations + edits 통합, 비율·해상도 UI |
| 4 | prompt caching + Haiku 메타 |
| 5 | 자동 다운로드 4종 + 재생성 5회 무료 + 측정 모드 |
| 6 | Free Trial 30명 + 어뷰징 1차 방어 |
| 7 | KPI 측정 인프라 + Cohort 1 초대 |

### 미해결 토론

- **기타2 SEO 중복 콘텐츠 방어**: 같은 주제·부위 이미지·글 반복 → 네이버·구글 demote 위험. 다음 conversation에서 재개. 보존 위치: `project_seo_duplication_pending.md`
- **Pro 종량제 단가**: 베타 1개월 후 실데이터로 산정 (예상 2,500~5,000원/세트)
- **영상 SaaS**: M6+ 별도 베타 (`project_video_deferred.md`)

## 주의사항

- 환자 식별 정보(이름, 주민번호, 연락처)는 로그에 출력 금지
- 처방 로직은 반드시 의료진 최종 확인 단계 포함
- 의료 기록 삭제는 소프트 딜리트(soft delete) 방식 사용
