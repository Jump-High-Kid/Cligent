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

## 현재 구현 상태 (2026-04-20 기준)

### 폴더 구조
```
medical-assistant/
├── run.py                  # 서버 시작 (python3 run.py)
├── conftest.py             # pytest 경로 설정
├── config.yaml             # 노코드 커스터마이징
├── requirements.txt
├── .env                    # API 키 + SECRET_KEY (gitignore)
├── .env.example
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
    └── test_agent_api.py       # ★ 5개 (API 엔드포인트)
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
- 재료 단계 T6 AI 바: `다른 AI로 작성 | Claude.ai | ChatGPT | Gemini | 📋 복사만`
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

## 주의사항

- 환자 식별 정보(이름, 주민번호, 연락처)는 로그에 출력 금지
- 처방 로직은 반드시 의료진 최종 확인 단계 포함
- 의료 기록 삭제는 소프트 딜리트(soft delete) 방식 사용
