# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 이름
**Cligent** (GitHub: https://github.com/Jump-High-Kid/Cligent)


## 프로젝트 개요

한의학(韓醫學) 보조 애플리케이션 **Cligent**. 한의사 임상 업무 지원을 목적으로 하는 의료 소프트웨어.

## 도메인 규칙

- **환자 데이터**: 개인정보보호법(PIPA) 및 의료법 준수 필수
- **한의학 용어**: 한글 + 한자 병기 (예: 변증(辨證), 기혈(氣血), 경락(經絡))
- **의학 정보**: 검증되지 않은 치료 효과는 사실로 제시 금지 — 항상 불확실성 명시
- **처방 데이터**: 약재명은 KCD 또는 표준 한의학 용어 사용

## 현재 구현 상태 (2026-04-19 기준)

### 폴더 구조
```
medical-assistant/
├── run.py                  # 서버 시작 (python3 run.py)
├── conftest.py             # pytest 경로 설정
├── config.yaml             # 노코드 커스터마이징
├── requirements.txt
├── .env                    # API 키 + SECRET_KEY (gitignore)
├── .env.example
├── agents/                 # Claude Code 에이전트 정의 (.md)
├── prompts/                # 프롬프트 텍스트 파일
├── data/
│   ├── cligent.db          # SQLite (users, invites, clinics)
│   ├── rbac_permissions.json
│   └── blog_history.json
├── src/
│   ├── main.py             # FastAPI 앱 (전체 라우트)
│   ├── auth_manager.py     # JWT, bcrypt, 초대 토큰
│   ├── db_manager.py       # SQLite 초기화 + 커넥션
│   ├── module_manager.py   # RBAC 권한 관리
│   ├── settings_manager.py # 설정 위자드 데이터
│   ├── blog_generator.py   # 블로그 SSE 스트리밍
│   ├── blog_history.py     # 생성 이력 저장
│   ├── conversation_flow.py
│   ├── image_prompt_generator.py
│   └── config_loader.py
├── templates/
│   ├── app.html            # ★ 앱 쉘 — 사이드바 고정 + iframe 내비게이션
│   ├── dashboard.html      # 메인 대시보드 (iframe 내 로드)
│   ├── dashboard_mobile.html
│   ├── login.html          # 로그인 + 비밀번호 변경
│   ├── onboard.html        # 초대 링크 온보딩
│   ├── index.html          # 블로그 생성기 (iframe 내 로드)
│   ├── settings.html       # 설정 (iframe 내 로드)
│   └── settings_setup.html # RBAC 초기 설정 위자드
└── tests/
    ├── test_blog.py
    └── test_auth.py        # 20개 유닛 테스트
```

### 인증 시스템 (2026-04-18 완성)
- **JWT httpOnly 쿠키** (8h 유효, SameSite=Lax)
- **5단계 RBAC**: chief_director > director > manager > team_leader > team_member
- **초대 기반 온보딩**: 원장이 링크 생성 → 카톡/문자 전달 → 직원 비밀번호 설정
- **슬롯 관리**: clinic당 max_slots 제한
- **SECRET_KEY**: 서버 시작 시 검증, .env 필수

### 블로그 생성기 (완성)
- **3단계 플로우**: 주제 입력 → 대화형 질문 → SSE 스트리밍 생성
- **이미지 프롬프트**: 블로그 완성 후 5개 자동 생성
- **복사 기능**: 네이버 서식 유지 HTML 복사

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

#### 이미지 프롬프트 생성 기능 (Phase B-1, 2026-04-16 추가)
블로그 생성 완료 후 "이미지 프롬프트 생성" 버튼으로 이미지 AI용 프롬프트 5개 자동 생성.

**이미지 프롬프트 조건 (prompts/image_prompt.txt 참조)**
1. 신뢰성 — 의료 공간의 전문성·청결함이 느껴질 것
2. 과장 금지 — 치료 효과를 단정하는 시각 요소 없음
3. 배경 단순화 — 단색·흐린 공간·자연 텍스처
4. 본문 맥락 밀착 — 각 프롬프트는 블로그 섹션과 직접 연결
5. 순차 배치 — 도입 → 원인 → 치료 → 생활 → 마무리 순서
6. 치료 클로즈업 1개 필수 — 침·뜸·한약 조제 등 클로즈업
7. 차분한 톤 — warm beige, natural wood, muted palette
8. 한의원 분위기 — 우드·한지·베이지 기반 미니멀 공간 (2025 트렌드 반영)

**의료 윤리 준수**
- 환자 얼굴 정면 클로즈업 금지 (측면·후면·손 허용)
- 처방전·의료 기록 노출 금지
- 특정 약재 치료 효과 암시 금지

**사용자 팁** — 이미지 AI에 실제 진료실 사진을 함께 제공하면 품질이 향상됨 (Midjourney `--iw`, DALL-E 이미지 편집, 나노바나나 참조 이미지)

#### 블로그 생성기 추가 개선사항 (2026-04-16)
- **복사 기능**: ClipboardItem API — HTML 형식 복사로 네이버 붙여넣기 시 서식(굵기·제목) 유지, 폴백 시 마크다운 기호 제거
- **이미지 프롬프트**: 한의사 흰 가운, 현대적 클리닉 인테리어(한옥 배경 제거), 상담실·진료실에 컴퓨터·모니터 배치
- **경혈 위치**: WHO Standard Acupuncture Point Locations 기준 ST36·LI4·PC6·SP6 등 9개 주요 경혈 해부학적 위치 명시
- **참고 자료**: 미주 URL 링크 추가 (확실한 URL만), "(정확한 권호 확인 필요)" 문구 제거 → 불확실 시 공란

#### 실행 방법
```bash
python3 run.py        # 서버 시작 → http://localhost:8000
python3 -m pytest tests/ -v   # 테스트 실행
```

#### BYOAI 모델
- 사용자가 본인 Anthropic API 키 직접 입력
- claude-sonnet-4-6 기준 글 1편 ≈ ₩7~14

### 앱 쉘 구조 (2026-04-19 확정)

**진입 흐름**: 로그인 → `/app` → `app.html` 쉘 로드 → iframe에 `/` (대시보드) 로드

- `GET /app` → `templates/app.html` (인증 필수)
- 사이드바는 `app.html`에만 존재, iframe 콘텐츠 페이지는 사이드바 숨김
- 각 페이지: `if (window.self !== window.top)` 감지 → `#sidebar` 숨김, 마진 0
- 로그인 후 리다이렉트: iframe 안→`/`, 직접 접속→`/app`
- localStorage `cligent_sidebar` = `'1'`(접힘) / `'0'`(펼침) — 새로고침 유지

**사이드바 메뉴 (정식 아이콘)**:

| 메뉴 | Material Symbol | 경로 |
|---|---|---|
| 대시보드 | `dashboard` | `/` |
| 블로그 생성기 | `article` | `/blog` |
| 재고 관리 | `inventory_2` | `#` |
| 스케줄 관리 | `calendar_today` | `#` |
| 고객 관리 | `group` | `#` |
| 설정 | `settings` | `/settings` |

### 설정 페이지 구조 (2026-04-19 확정)

`templates/settings.html` — 6개 탭 구성:

| 탭 | 설명 | 상태 |
|---|---|---|
| 팀 & 권한 관리 | 직원 목록 + 모듈 권한 토글 + 초대 모달 | 완성 |
| 콘텐츠 에이전트 | 블로그·SNS 자동화 설정 | 향후 구현 |
| 스케줄 관리 | 직원 근무 스케줄 설정 | 향후 구현 |
| 재고 관리 | 약재·물품 재고 설정 | 향후 구현 |
| 문헌 정리 | 한의학 문헌 수집·분류 설정 | 향후 구현 |
| 시스템 & 보안 | **대표 원장 전용** — 아래 5개 하위 항목 포함 | 향후 구현 |

**시스템 & 보안 하위 항목 (대표 원장만 접근 가능):**
- 한의원 프로필
- AI 설정 (API Key, 모델 선택, 월 예산)
- 플랜 & 사용량
- 보안
- 데이터 관리

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

### 다음 구현 예정: 멀티 AI 모델 지원
설정 > 시스템 & 보안 > AI 설정 탭으로 구현 예정. 3단계 계획:
- **Phase 1** — 설정 AI 설정 탭: 공급사별 API 키 저장(암호화), 기본 모델 선택, 월 예산 설정
- **Phase 2** — 블로그 생성기 모델 선택 적용: 선택 모델에 따라 OpenAI/Anthropic/Gemini API 분기
- **Phase 3** — 멀티모델 비교 패널: 동일 주제로 2~3개 모델 병렬 생성·비교

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

## 주의사항

- 환자 식별 정보(이름, 주민번호, 연락처)는 로그에 출력 금지
- 처방 로직은 반드시 의료진 최종 확인 단계 포함
- 의료 기록 삭제는 소프트 딜리트(soft delete) 방식 사용
