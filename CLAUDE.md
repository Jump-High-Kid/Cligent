# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **변경 이력**: 날짜별 구현 상세는 [docs/CHANGELOG.md](docs/CHANGELOG.md) 참조. 본 파일은 영구 아키텍처/현재 운영 정보만 유지.

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
- **환자 식별 정보**(이름·주민번호·연락처) 로그 출력 금지
- **처방 로직**은 의료진 최종 확인 단계 포함
- **의료 기록 삭제**는 소프트 딜리트(soft delete) 방식

## 진행 중 (2026-05-02)

> 상세는 CHANGELOG 항목 참조.

- **v0.9.0 라우터 분할 (4/6 — blog C1)** — main.py 4,000줄 → 6 도메인 라우터(auth/clinic/billing/blog/dashboard/admin). 베타 진행+영상/재고 모듈 추가 대비. auth.py 18 라우트(-503), clinic.py 25 라우트(-529), billing.py 2 라우트(-104), blog.py C1 24 라우트(-653) 완료. main.py 4,021 → 2,232(-1,789, -44.5%). blog C2(SSE 3건: /api/blog-chat/turn, /generate, /generate-image-prompts) + dashboard + admin 남음.
  - SemVer 도입: VERSION 파일 단일 진실원, /api/version, 사이드바·어드민 footer 표시.
  - 공용 의존성: `src/dependencies.py` (is_admin_clinic, require_admin_*, NO_CACHE_HEADERS).
  - 공용 암호화: `src/crypto_utils.py` (_get_fernet, encrypt_key/decrypt_key/mask_key). main.py에 _encrypt_key/_decrypt_key/_mask_key/`_get_fernet` alias 보존(어드민 OpenAI 키 + tests/test_onboarding monkeypatch 호환).
- **해부학 DB Phase 1 인프라** — 30 부위 자료 수집 인프라 완성, 도메인 작업 1주 일정. 베타 critical path. Cohort 1 노출 게이트 ②번.
  - **다중 view 지원 (2026-05-01)**: 부위당 자료 1개 → 여러 view 공존. 파일명 `source_{view}.{ext}` + `meta_{view}.json`. validate 진행률은 부위 단위(30 기준) 유지. 어깨 anterior + posterior 2자료 등록(1/30).
- **블로그 챗 UI 단일 진입점** — `/blog`가 `templates/blog_chat.html` 챗 UI 사용. 4단계 폼(`index.html`) dead code.
- **이미지 모듈 시스템 (`src/image_modules.py`)** — 11 모듈 분기, 5장 모두 다른 모듈, negative 본문 통합. gpt-image-2 generations / gpt-image-1.5 edits.
- **자료 변형 자동화 보류**: ChatGPT 웹 직접 변환이 현 시점 최선. `scripts/edit_anatomy_demo.py` 보존(Phase 2 출발점), `_demo/` 결과물은 `.gitignore`.

## 폴더 구조 요약

- `run.py` (서버 시작), `conftest.py`, `config.yaml`, `requirements.txt`, `.env(.example)`
- `docs/CHANGELOG.md` — 날짜별 구현 이력 (이 파일에서 분리)
- `scripts/` — `create_clinic.py`, `create_demo_account.py`, `reset_password.py`, `backup.sh`, `init_anatomy_part.py`, `fetch_anatomy.py`, `validate_anatomy_meta.py`, `edit_anatomy_demo.py`(Phase 2 참고용)
- `data/` — `cligent.db`, `anatomy/{slug}/meta_{view}.json` + `source_{view}.png`, `blog_stats.json`(영구), `blog_texts.json`(30일 TTL), `pending_checks.json`, `academic_cache.json`(24h), `error_logs/{date}.jsonl`, `agent_log.jsonl`
- `prompts/` — `blog.txt`, `blog_patterns.txt`(서론 7+본론 8+결론 7+화제전환 6), `image_analysis.txt`(Stage1), `image_generation.txt`(Stage2), `formats/`(6종), `references/sasang.txt`, `agents/`(8개)
- `src/` 핵심:
  - **공통**: `main.py`, `auth_manager.py`, `db_manager.py`, `module_manager.py`, `settings_manager.py`, `secret_manager.py`(Fernet), `observability.py`(Sentry+structlog+PII), `plan_guard.py`(60s TTL), `plan_notify.py`, `usage_tracker.py`
  - **AI 래퍼**: `ai_client.py`(Anthropic+OpenAI), `metadata_generator.py`(Haiku 4.5)
  - **블로그**: `blog_generator.py`(SSE+RAG), `blog_history.py`, `blog_chat_flow.py`, `blog_chat_options.py`, `pattern_selector.py`, `format_selector.py`, `hook_selector.py`, `citation_provider.py`, `academic_search.py`(jkom·PubMed·Naver doc)
  - **이미지**: `image_modules.py`(11 모듈), `image_generator.py`(한도/해상도), `image_session_manager.py`, `image_prompt_generator.py`(2단계)
  - **기타**: `naver_checker.py`, `agent_router.py`+`agent_middleware.py`(베타 미사용)
- `static/` — `favicon.svg`, `og-image.png`(1200×630), `legal.css`, `uploads/announcements/`
- `templates/` — `app.html`(쉘), `landing.html`, `blog_chat.html`(현재 `/blog`), `index.html`(dead code), `dashboard.html`, `chat.html`(베타 비활성), `help.html`, `login.html`, `onboard.html`, `settings*.html`, `join.html`, `announcements*.html`, `admin_*.html`(8개), `legal/{terms,privacy,business}.html`

## 기술 스택

- **백엔드**: Python 3.9 + FastAPI 0.115
- **AI**: Anthropic SDK (claude-sonnet-4-6 본문, claude-haiku-4-5 메타) + OpenAI (gpt-image-2 / gpt-image-1.5)
- **프론트엔드**: Vanilla JS + HTML
- **DB**: SQLite (`data/cligent.db`)
- **테스트**: pytest + FastAPI TestClient + unittest.mock

**Python 3.9 호환 주의:**
- `Optional[dict]` 사용 (3.10+ `dict | None` 불가)
- lazy import 패치는 `db_manager.get_db` 경로

## 핵심 아키텍처

### 인증 시스템
- **JWT httpOnly 쿠키** (8h, SameSite=Lax)
- **5단계 RBAC**: chief_director > director > manager > team_leader > team_member
- **초대 기반 온보딩**: 원장 → 링크 생성 → 카톡/문자 → 직원 비밀번호 설정
- **슬롯 관리**: clinic당 max_slots 제한
- **SECRET_KEY**: 서버 시작 시 검증, .env 필수

### 앱 쉘 구조
- 로그인 → `/app` → `app.html` 쉘 로드 → iframe에 `/dashboard` 로드
- 사이드바는 `app.html`에만 존재. iframe 페이지는 `if (window.self !== window.top)` 감지 → 사이드바 숨김, 마진 0
- 로그인 후 리다이렉트: iframe 안→`/`, 직접 접속→`/app`
- localStorage `cligent_sidebar` = `'1'`(접힘) / `'0'`(펼침)
- 모바일 (`max-width: 767px`): 사이드바 숨김, 하단 `#mobile-nav` 4탭(대시보드/블로그/공지/설정)

**사이드바 메뉴 (정식 아이콘)**:

| 메뉴 | Material Symbol | 경로 | 상태 |
|---|---|---|---|
| 대시보드 | `dashboard` | `/dashboard` | 완성 |
| 블로그 생성기 | `article` | `/blog` | 완성 |
| AI 도우미 | `chat` | `/chat` | 베타 비활성 |
| 재고 관리 | `inventory_2` | `#` | Coming Soon |
| 스케줄 관리 | `calendar_today` | `#` | Coming Soon |
| 고객 관리 | `group` | `#` | Coming Soon |
| 설정 | `settings` | `/settings` | 완성 |
| 도움말 | `help_outline` | `/help` | 완성 |

### 블로그 생성기
- **챗 UI 흐름**: TOPIC → LENGTH → QUESTIONS×4 → SEO → CONFIRM_IMAGE → GENERATING → IMAGE
- **글자 수**: 기본(2000자)/가벼운(1500자)/상세(2500~3000자)/직접 입력(최대 9999자)
- **RAG 학술 검색**: jkom + PubMed + Naver doc 3소스 병렬, 24h 디스크 캐시. 0건이면 "학술 논문 형식 인용 작성 금지" 주입
- **참고 문헌**: RAG 결과만 인용, 가상 논문 차단
- **의료법 고지문 자동 삽입**: 의료법 56·57조, 시행령 23·24조 (`_inject_legal_disclaimer()`)
- **사상체질 분기**: `prompts/references/sasang.txt` 자동 주입, 변증시치와 상호 배타
- **베타 제한**: 블로그 10건 / 프롬프트 30건 (누적, plan_guard.py)
- **개인정보 1회 모달**: 2단계 첫 진입 시 세션당 1회

### 이미지 프롬프트 생성 (2단계 파이프라인)
- Stage 1: `image_analysis.txt` — 블로그 분석 → 장면 계획 JSON (모듈 분류, 경혈 자동 선택, anatomical_region)
- Stage 2: `image_generation.txt` — JSON → 영어 프롬프트 배열 JSON
- `image_modules.py` 11 모듈 fragment를 영어 프롬프트에 통합
- 5장 모두 다른 모듈 — `blog_chat_flow.py`가 5번 직접 호출
- negative_prompt 본문 통합 (gpt-image-2 별도 인자 없음 — `IMAGE_INJECT_NEGATIVES=0`로 끔)
- 단일 이미지 강제 (`generate as single standalone image, do not combine into grid/mosaic/collage`)
- 경혈 30개 (WHO 기준): LU7, LI4, LI11, ST25/36/40/44, SP6/10, HT7, SI3, BL17/23/40/60, KD3/6, PC6, TE5, GB20/21/34, LV3, GV4/14/20, CV4/6/12/17

### 이미지 세션 + 한도
- **모델 분리**: generations=`gpt-image-2`, edits=`gpt-image-1.5` (gpt-image-2 edit 미지원)
- **BytesIO 파일명 필수**: `.name="input.png"`/`.name="mask.png"` (multipart)
- **플랜별 한도**: Standard 재생성 1회 + 수정 2회 / Pro 재생성 2회 + 수정 4회 / trial=Standard / free 0
- **플랜별 해상도**: Standard 1024×1024 medium ($0.053/장) / Pro 1536×1024 high ($0.165/장)
- DB: `image_sessions` (session_id UUID4, plan_id_at_start 등). PermissionError로 다른 클리닉 차단
- 429 응답: `{kind:"quota_exceeded", type, plan_id, used, limit, message}`

### 결제 시스템 Phase 1
- **플랜**: free(월 3편) / standard(무제한, 29,000원) / pro(무제한, 59,000원) — *베타 기간 가격은 추후 공지*
- `plan_guard.resolve_effective_plan()` 함수가 3곳에서 공유:
  - `plan_guard.check_blog_limit()` — 블로그 차단
  - `plan_notify._notify_worker()` — 80% 알림
  - `main.get_plan_usage()` — 설정 사용량 표시
- 우선순위: `plan_expires_at` → `trial_expires_at` → 무료 월 3편
- trial abuse 방어: `trial_expires_at` 재설정 코드 없음
- DB: `plans` + `usage_logs` + `subscriptions`(빈 셸) + `clinics` 컬럼 4종

### 베타 모집 + 초대 발송
- DB: `beta_applicants` (9 컬럼 추가: application_type, marketing_consent, consented_terms_version, ip_address, user_agent, rejection_reason, admin_notes, admin_tags, expires_at). status: pending/invited/registered/rejected/expired.
- DB: `applicant_emails` — 모든 발송 ledger
- API: `POST /api/beta/apply` (IP 레이트 리밋 5분/3회), `/join`, `/admin/applicants`, `/api/admin/invite-batch` (Semaphore 5)
- 이메일: E1 신청확인 / E2 어드민알림 / E3 초대링크 / E4 72h 리마인더 / E5 clicked_at / D3 status='registered'
- 공통 헬퍼 `_send_smtp(to, subject, html_body, applicant_id, email_type) → bool` (fail-soft)

### 어드민 패널
- 진입: 세션(chief_director + ADMIN_CLINIC_ID) **또는** `ADMIN_SECRET` Bearer (`_require_admin_or_session`)
- 페이지 8개: `/admin` 인덱스, `/admin/applicants`, `/admin/clinics`, `/admin/usage`, `/admin/feedback`, `/admin/login-history`, `/admin/errors`, `/admin/blogs`, `/admin/settings`
- nav 통일: 어드민 / 신청자 / 한의원 / 사용량 / 피드백 / 로그인 이력 / 에러 / 블로그 / 설정

### lifespan 스케줄러 6종 (모두 24h 주기)
- 데일리 리포트 / E4 베타 리마인더(72h 미클릭) / 네이버 발행 확인
- 신청자 30일 만료 / 로그인 이력 90일 정리 (PIPA) / 에러 로그 90일 정리

### 로그인 이력 (PIPA 90일)
- DB: `login_history` (raw email/clinic_id/ip/user_agent/success/failure_reason/created_at)
- `auth_manager.authenticate_user` → `(user, failure_reason)` 튜플 (user_not_found / invalid_credentials / password_not_set / disabled)
- `record_login_attempt()` fail-soft (모든 예외 흡수)
- 의심 IP 자동 감지: 1시간 내 동일 IP 5회 이상 실패
- 사용자 본인: `GET /api/auth/login-history` (최근 90일 50건)
- **SQLite native datetime 비교 필수**: `datetime(created_at) >= datetime('now', ?)`

### 공지사항 게시판
- DB: `announcements` / `announcement_reads` / `announcement_attachments`
- 본문: marked.js + DOMPurify 클라이언트 markdown 렌더
- 이미지 첨부 5MB(jpg/png/webp/gif), `static/uploads/announcements/`
- 카테고리 3종(업데이트/점검/일반), 상단 고정, 안 읽은 뱃지
- 권한: `_require_announce_admin` (chief_director + ADMIN_CLINIC_ID)
- **공지 작성 정책**: 외부 사용자용 — 내부 구현 상세(파일명·버전·정책 수치) 제외

### 네이버 발행 확인
- `src/naver_checker.py` Naver Search API blog.json 폴링
- `data/pending_checks.json` 백그라운드 폴링 (60m → 120m×5 → 360m×4 → 720m, 7일 만료)
- API: `POST /api/blog/history/{entry_id}/publish-check`, `GET /api/blog/notifications`, `POST /api/blog/notifications/{id}/dismiss`
- 발행 상태 4단계: 미등록 / 대기 중 / ✓ 발행 확인됨 / ! 누락
- 어드민: `templates/admin_settings.html` Naver Client ID/Secret (`data/app_settings.json`)

## 설정 페이지 구조

`templates/settings.html` — 6개 탭:

| 탭 | 상태 |
|---|---|
| 팀 & 권한 관리 | 완성 (직원 목록 + 모듈 권한 토글 + 초대/재초대) |
| 콘텐츠 에이전트 | 완성 (블로그 설정 + 생성 이력) |
| 스케줄 관리 / 재고 관리 / 문헌 정리 | 향후 구현 |
| 시스템 & 보안 | 부분 완성 |

**시스템 & 보안 서브탭**:
- 한의원 프로필 ✅
- AI 설정 ✅ (API 키 Fernet 암호화, 모델 선택, 월 예산)
- 플랜 & 사용량 / 보안 / 데이터 관리 — 준비 중 (또는 부분)

**비밀번호 재설정 흐름**: 설정 > 팀 > "재설정 링크 생성" → `POST /api/settings/staff/{id}/reinvite` → 72h 토큰 → `/onboard?token=...`

**모듈 권한 토글 동작**:
- `chief_director` / `director` 선택 → 모든 토글 `disabled=true` ("항상 접근")
- `team_member` 이하 → 자유 토글 + 즉시 자동저장 (`POST /api/settings/staff/modules`)

## 디자인 시스템 원칙 (2026-04-19 확정)

> **핵심 규칙**: 대시보드 또는 사이드 패널 디자인이 변경되면, 모든 하위 페이지(설정·블로그 생성기 등)에 동일하게 반영.

### 디자인 토큰

| 항목 | 값 |
|---|---|
| 사이드바 배경 | `bg-stone-100` |
| 활성 메뉴 | `bg-emerald-900 text-white rounded-xl` |
| 비활성 메뉴 텍스트 | `text-stone-600` |
| 비활성 메뉴 호버 | `hover:bg-stone-200` |
| 아이콘 스타일 | `wght 300, FILL 0, GRAD 0, opsz 24` |
| 폰트 | Pretendard (본문), Manrope (헤드라인) |
| 주색 | `emerald-900` (#064e3b) |
| 보조색 | sage (`--sage:#a8b5a0`, `--sage-soft:#eef1ea`, `--sage-tint:#f6f8f4`) |

### 사이드바 통일 원칙
- **기준 파일**: `dashboard.html` canonical
- 토글 CSS: `.ios-toggle:checked ~ .ios-toggle-dot` (`+` 아님, `~`)
- 하단 구성: `role-badge` → `invite-btn`(director 이상만) → `doLogout()`
- collapsed 숨김: `nav-label`, `sidebar-logo-text`, `sidebar-role`, `sidebar-invite-label`

### 새 페이지 추가 체크리스트
- [ ] `app.html` 사이드바 메뉴 추가 (`data-path` 속성)
- [ ] FastAPI 라우트 추가 (`src/main.py`)
- [ ] 페이지 HTML iframe 감지 코드:
  ```js
  if (window.self !== window.top) {
    document.getElementById('sidebar').style.display = 'none';
    document.getElementById('main-content').style.marginLeft = '0';
  }
  ```
- [ ] 폰트: Pretendard (`cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9`)
- [ ] Material Symbols: `wght,FILL@100..700,0..1` range

## 가격·이미지 구조 v7 결정 (2026-04-30)

> 상세: 메모리 `project_cligent_pricing_v7.md` + `project_image_strategy.md`

### 핵심 결정
- **BYOAI 위자드 베타 비활성** (M3+ Lite로 단계 재도입). 베타는 All-in 정액제 단일.
- **가격**: Standard 14.9만(30건) / Pro 27.9만(80건+종량제) — 쏙AI(19.9만/90건) 대비 위·아래 협공
- **3개월 코호트**: 5인 무료 → 25인 1만원 → 50인 15% 할인 → 정식 M3+
- **이미지 모델**: gpt-image-2 단일. Standard 1024×1024 medium / Pro 1536×1024 high
- **Pro 출시 조건부**: 해부학 DB Phase 2 (100 부위) + 평균 재생성 1.5회 이하 + 만족도 80%
- **재생성·수정**: Standard 1+2 무료 / Pro 2+4 무료. 초과 종량제 (베타 후 단가 산정)
- **Hybrid AI**: 본문 Sonnet 4.6 / 메타 Haiku 4.5 (즉시 도입)
- **edit endpoint 우선** (재생성 대신 부분 수정 → 세션 총비용 35% ↓)

### 해부학·경혈 DB가 사업 성패 lever
- 비용 1/3 절감 + Pro 가격 정당성 + visual moat
- 자료 출처: Servier Medical Art (CC-BY 3.0), BodyParts3D, Wikimedia, WHO 표준 경혈
- Phase 1 (M0~M2) 30 부위 + 240 경혈 좌표 = **원장님 도메인 작업, critical path**

### 미해결 토론
- **기타2 SEO 중복 콘텐츠 방어**: 같은 주제·부위 반복 → 네이버·구글 demote 위험. `project_seo_duplication_pending.md`
- **Pro 종량제 단가**: 베타 1개월 후 산정 (예상 2,500~5,000원/세트)
- **영상 SaaS**: M6+ 별도 베타 (`project_video_deferred.md`)

## 노코드 커스터마이징 (config.yaml)

```yaml
flow:
  questions_enabled: true   # 질문 단계 on/off
  questions_count: 3
blog:
  min_chars: 1500
  max_chars: 2000
  tone: "전문적"
prompts:
  questions: "prompts/questions.txt"
  blog: "prompts/blog.txt"
providers: ["riss","kci","google_scholar","pubmed"]
```

## 개발 환경

```bash
# 첫 설치
python3 -m pip install -r requirements.txt

# 서버 시작
python3 run.py        # → http://localhost:8000

# 테스트
python3 -m pytest tests/ -v
```

- `~/Library/LaunchAgents/kr.cligent.app.plist` — launchd KeepAlive (재부팅 후 uvicorn 자동 재시작)
- 포트 충돌 시: `launchctl unload ~/Library/LaunchAgents/kr.cligent.app.plist`
- `.env` 첫 줄 탭 문자 주의 — 환경변수 미인식 원인. `load_dotenv(ROOT / ".env", override=True)`

## 프로덕션 배포 체크리스트

### 필수 환경 변수
- [ ] `ENV=prod` — 미설정 시 dev. dev 모드는 서버 시작 시 `seed_demo_clinic()` 실행 (trial_expires_at 없는 데모 클리닉 생성)
- [ ] `SECRET_KEY` — 미설정 시 서버 시작 실패 (의도적 fast-fail)
- [ ] `ANTHROPIC_API_KEY` — 미설정 시 블로그 생성 실패
- [ ] `ADMIN_SECRET` — 미설정 시 `/api/admin/clinic` 비활성화 (403)
- [ ] `ADMIN_CLINIC_ID`, `ADMIN_USER_ID` — 시드 어드민 계정
- [ ] `ADMIN_NOTIFY_EMAIL` — 신청 알림 수신
- [ ] `BASE_URL` — 초대 링크 도메인 (https://cligent.kr)
- [ ] SMTP 5종: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_FROM` — 미설정 시 알림 비활성 (로그만, 서비스 영향 없음)

### 신규 한의원 생성 (베타 참가자 등록)
trial_expires_at(14일)은 아래 두 방법 중 하나로 설정. `seed_demo_clinic()` 또는 직접 DB INSERT는 사용 금지.

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

## 인프라 현황

- **도메인**: cligent.kr, cligent.co.kr (가비아 DNS A → 61.76.131.91)
- **서버**: 맥북 로컬 (192.168.50.132)
- **공유기**: ASUS RT-ACRH13 — 80/443 포트포워딩
- **리버스 프록시**: Caddy 2.11.2 (`/opt/homebrew/etc/Caddyfile`)
- **SSL**: Let's Encrypt 자동 발급
- **접속 URL**: https://cligent.kr

### Caddy 관리
```bash
brew services restart caddy
brew services info caddy
cat /opt/homebrew/etc/Caddyfile
```

### 보안 헤더 (Caddyfile)
```
header {
    Strict-Transport-Security "max-age=300"
    X-Content-Type-Options "nosniff"
    Referrer-Policy "strict-origin-when-cross-origin"
    X-Frame-Options "SAMEORIGIN"
}
```
includeSubDomains·preload 미사용. 단계 상향: 1주 후 86400, 1개월 후 31536000.

## SEO 인프라

- `GET /robots.txt` — 공개 페이지 허용, `/api/`·`/admin`·`/app`·`/dashboard`·`/blog`·`/chat`·`/settings`·`/onboard`·`/login`·`/forgot-password`·`/youtube`·`/help` 차단
- `GET /sitemap.xml` — 공개 4개 URL (/, /terms, /privacy, /business)
- `landing.html` head: canonical / OG 풀세트 / Twitter Card / JSON-LD `@graph` (Organization + WebSite + SoftwareApplication)
- Google Search Console 인증 완료 (토큰: `1W24HKtWNVkWebhUsc-IyUG6TjyeVVNm3WN1Tpwb8dg`)
- Naver Search Advisor 인증 완료 (토큰: `4f52868ae171c9987fd900323d156bc39f74b4d3`)

**알려진 이슈 — 모바일 Chrome**: cligent.kr이 모바일 Chrome 미접속 (데스크톱·모바일 Naver 정상). 가설: HTTP/3 QUIC이 ASUS UDP 443 미통과. 임시 해결: Caddy 전역 `protocols h1 h2` 또는 라우터 UDP 443 포트포워딩.
