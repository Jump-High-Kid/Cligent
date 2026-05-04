# TODOS — Cligent

## P1 (완료)

### 1. agents/ + src/ 폴더 분리 ✅
**완료일**: 2026-04-18
**What**: 루트의 `.md` 파일들을 `agents/`로 이동, FastAPI 코드는 `src/`에 시작.

---

### 2. 시스템 프롬프트 설계 (MVP 핵심) ✅
**완료일**: 2026-04-16
**What**: prompts/ 폴더에 blog.txt, questions.txt, image_prompt.txt 구현

---

### 3. 인증 시스템 구현 ✅
**완료일**: 2026-04-18
**What**: JWT httpOnly 쿠키, 5단계 RBAC, 초대 기반 온보딩, 슬롯 관리

---

### 4. 대시보드 로그아웃 + 직원 초대 UI ✅
**완료일**: 2026-04-18
**What**: 로그아웃 버튼, 직원 초대 모달 (이메일 + 역할 선택 + URL 복사)

---

## P1 — 베타 런치 트랙 (2026-04-27 결정)

> CEO plan: `~/.gstack/projects/Jump-High-Kid-Cligent/ceo-plans/2026-04-27-beta-launch-track.md`
> 권장 순서: 백업 → 모니터링 → 약관 → 랜딩+도메인 → 피드백 강화

### B1. 일일 rsync 백업 ✅ (2026-04-27 완료)
**완료**: `scripts/backup.sh` + `~/Library/LaunchAgents/kr.cligent.backup.plist` 가동. 복원 검증 OK. 복원 명령은 메모리 `project_backup_system.md` 참조.
**What**: launchd 일일 job으로 SQLite + .env + prompts/ + data/blog_texts.json → iCloud Drive(또는 외장 SSD).
**Why**: Fernet 키 1번 잃으면 베타 사용자 전원 API 키 재입력 필요 — 신뢰 손상. 맥북 SSD 단일 장애점.
**Files**: `scripts/backup.sh` 신규, `~/Library/LaunchAgents/kr.cligent.backup.plist` 신규.
**Critical edges**:
- SQLite WAL 모드 → `sqlite3 cligent.db ".backup"` 명령 필수 (단순 cp 금지)
- Fernet 키 평문 백업 금지 → gpg 또는 비번 zip
- iCloud sync 락 충돌 → 비동기화 폴더 권장
**Test**: 백업 → 빈 폴더에서 복원 → DB 열림 + 로그인 검증.
**Effort**: CC ~30분.

---

### B2. 모니터링 + 로깅 (Sentry + structlog + 일일 메트릭) ✅ (2026-04-27 완료)
**완료**: `src/observability.py` + `src/main.py` 미들웨어 + `src/daily_report.py` 메트릭 섹션. PII 마스킹(user_id 해시 / api_key·password REDACTED / env vars REDACTED) 검증 완료. Sentry 프로젝트: `python-fastapi`. 운영 명령은 메모리 `project_observability_system.md` 참조.
**What**: structlog 미들웨어(json line, request_id) + Sentry SDK(free tier) + `daily_report.py` 확장.
**Why**: 5인 동시 사용 시 print 로그로는 디버그 불가. 외부 베타 안정성 핵심.
**Files**: `src/main.py`(미들웨어), `requirements.txt`(structlog/sentry-sdk), `src/daily_report.py` 확장, `.env`(SENTRY_DSN).
**Critical edges**:
- PII 누출 방지 → Sentry `before_send` hook으로 한의원명/이메일/API 키 자동 마스킹 (PIPA 위반 방지)
- launchd가 stdout 안 모음 → structlog가 `/var/log/cligent/app.log`로 직접 기록
**Test**: 의도적 ZeroDivisionError → Sentry 대시보드 확인 + PII 마스킹 검증.
**Effort**: CC ~30분 + Sentry 계정 생성.
**Depends on**: B1 완료 후.

---

### B3. 약관 / 개인정보처리방침 / 사업자정보 ✅ (2026-04-27 완료)
**완료**: `templates/legal/{terms,privacy,business}.html` + `static/legal.css` + `src/main.py` 라우트 3개. 사용자 보강 반영(베타 단계 대표원장 1인 한정, 양도·판매·대여·공유 금지). 백엔드 차단(`_is_admin_clinic`) + UI 차단(4개 템플릿 `can_invite` 조건) 일관성 확보. 자가 비밀번호 재설정(`/forgot-password`) 추가 — enumeration 방지·rate limit·HTML 메일·기존 invite 토큰 재사용. 대표원장 분실은 `scripts/reset_password.py` 운영 도구. 변호사 검토는 정식 출시 전 별도.
**What**: 정적 HTML 3장 + 라우트 3개. 추후 회원가입 시 동의 체크박스로 연결.
**Why**: 가입 폼 만드는 순간 PIPA 22조(수집·이용 동의), 26조(위탁업체 고지), 의료법 19조 적용.
**Files**: `templates/legal/{terms,privacy,business}.html` 신규, `src/main.py` 라우트 3개.
**Critical edges**:
- 위탁업체 명시 필수 → Anthropic/OpenAI/Google에 데이터 전달 사실 명시
- 30일 TTL 보관 기간 → 코드와 약관 일치 (변경 시 동기화)
- 사업자등록 미완료라면 → "준비 중" 표시 후 정식 오픈 전 갱신
- 변호사 검토 미완료 → "최종 검토 진행 중" 표시 (정식 오픈 시 제거)
**Test**: 브라우저 직접 열기 + 모바일 폭 확인.
**Effort**: CC ~20분 초안 + 사용자 검토 시간.
**Depends on**: B2 완료 후. 사용자 검토 후 배포.

---

### B4. 랜딩 페이지 + 도메인 분할
**What**: cligent.kr → 마케팅 랜딩, app.cligent.kr → 실제 SaaS 앱, cligent.co.kr → 301 redirect.
**Why**: 외부 5인 모집 전 마케팅 entry point 필수. SEO/광고/회원가입 입구 확보.
**Files**:
- 신규: `templates/landing.html` (히어로/문제/솔루션 데모/가격/베타폼/FAQ/footer)
- 수정: `src/main.py` (호스트 헤더 분기 또는 Caddy reverse_proxy 분리)
- 수정: `/opt/homebrew/etc/Caddyfile`
- 가비아 DNS 패널: `app.cligent.kr` A 레코드 추가
**Critical edges (가장 위험)**:
- 🚨 JWT 쿠키 `Domain=.cligent.kr` (서브도메인 공유) — 빼먹으면 로그인 전부 깨짐
- 🚨 CORS — 랜딩 베타폼이 `/api/beta/apply` 호출 시 cross-origin 처리
- 🚨 기존 사용자 북마크 (`cligent.kr/app`) → `app.cligent.kr` 자연스럽게 안내
- ⚠️ SSL 갱신 — Caddy 자동, 첫 발급 시 1~2분 다운 가능
- ⚠️ DNS 전파 5~30분 → 새벽 시간 작업 + 사전 공지
- ⚠️ SEO — 랜딩에 sitemap.xml/robots.txt 신규, 앱은 noindex
**Rollback**: Caddyfile + DNS A 레코드 백업 → 5분 안에 원복 가능하게 미리 백업.
**Test**: DNS 전파 후 4가지 시나리오 (랜딩→가입, app→로그인, co.kr→301, 기존 쿠키 인식).
**Effort**: CC ~1시간 + DNS 전파 대기.
**Depends on**: B3 완료 후 (footer에 약관 링크 박혀야).

---

### B-ANATOMY. 해부학 이미지 DB — 점진 누적 (베타 1·2 백그라운드)
**상태 (2026-05-04)**: 진행률 2/30 (어깨 anterior+posterior, 요부 lateral+anterior+posterior).
**What**: 30 부위 × 다중 view 자료 + 240 경혈 좌표 누적. 부위당 자료 1개 → 여러 view 공존 (`source_{view}.{ext}` + `meta_{view}.json`).
**Why (게이트 제외 결정 2026-05-04)**: 자료 수집은 원장님 수동 작업 — 출처 확인·라이선스·view 분기·검증 모두 사람이 직접 처리. 코드로 가속 불가능. **Cohort 1 게이트 박아두면 베타 일정이 사람 작업 속도에 종속** → 베타 1·2 진행 중 점진 누적으로 전환.
**진행 방식**: 시간 날 때 부위 1~2개씩 추가. weekly 점검은 부위 수 트렌드만 (절대치 아님). 베타 1 노출 시점 = "있는 만큼"으로 시작, 베타 2 종료 시점에 25~30 도달 목표.
**Files (인프라 완료)**:
- `scripts/init_anatomy_part.py` — 부위 슬롯 생성
- `scripts/fetch_anatomy.py` — Playwright 다운로드
- `scripts/validate_anatomy_meta.py` — 메타 검증
- `data/anatomy/{slug}/meta_{view}.json` + `source_{view}.{확장자}`
**Critical edges**:
- 라이선스: Servier Medical Art (CC-BY 3.0), BodyParts3D, Wikimedia, WHO 표준 경혈 — 출처 100% 명시
- 다중 view: 진행률은 부위 단위(30 기준) 유지. anterior + posterior 둘 등록도 1 부위로 카운트
- Pro 가격 정당성: Phase 2(100 부위)는 별도 마일스톤 — Pro 출시 조건부 (만족도 80% + 평균 재생성 1.5회 이하 동시 충족 시)
**Depends on**: 없음. 다른 작업과 병행.
**메모리**: `project_anatomy_db.md`, `project_anatomy_db_multi_view.md`, `project_beta_gate.md`(게이트 제외 결정).

---

### B5. 베타 피드백 시스템 강화
**What**: 구조화 설문 모달(NPS/만족도/우선순위, 블로그 5회 사용 후 1회) + 행동 추적(SHA-256 익명) + 인터뷰 트리거(10건+ 사용자 자동 픽업).
**Why**: 30인 wave 단계 Phase 2 우선순위 결정 데이터. 사용자 명시 — 추측 빌드 금지, 데이터 기반.
**Files**:
- 수정: `templates/index.html` (NPS 모달), `src/main.py` (`POST /api/feedback/survey`), `src/usage_tracker.py` 확장, `src/daily_report.py` (인터뷰 트리거)
- 신규: `data/survey.jsonl`
**Critical edges**:
- 사용자 ID 해시 → SHA-256(user_id + salt), salt는 .env (rainbow 공격 방어)
- 설문 노출 빈도 → 한 번 닫으면 localStorage 30일 캐시
- 5인 단계 샘플 사이즈 → 통계보다 인터뷰가 진짜 시그널
- 인터뷰 트리거 카톡 → 본인한테만 (사용자 자동 발송 X, 동의 받지 않은 상태)
**Test**: 데모 계정 5회 사용 → 모달 노출 → 응답 → `data/survey.jsonl` 확인.
**Effort**: CC ~1시간.
**Depends on**: B4 완료 후. 5인 베타 모집 직전.

---

## P1.5 — 베타 2 (Cohort 2) 트랙 (2026-05-04 추가)

> **Trigger**: Cohort 1 (5인 베타) 종료 후 25인 모집 직전. 외부 사용자 본격 유입 단계.
> **Why now (등록만)**: 어뷰징 방어·결제 준비는 외부 의존성·법적 검토 시간 김. 베타 1 종료 직전에 코드만 봐도 늦음 → 일정 역산 위해 미리 항목화.
> **Why defer (구현)**: 베타 1 critical path(이미지 생성·해부학 DB 30 부위·게이트 4종)를 흔들지 않기 위해.

### C1. 한의원 정보 — 사업자등록증 업로드 UI
**What**: `templates/settings.html` `data-section="clinic"` (한의원 정보) 내부에 사업자등록증 업로드 카드 추가. 파일 업로드(jpg/png/pdf, ≤5MB) + 사업자등록번호 입력 + 검토 상태 뱃지(미제출 / 검토 중 / ✓ 인증 완료 / ✗ 반려).
**Why**: Cohort 2 어뷰징 방어 1차 게이트(`project_cligent_pricing_v7.md:107`). 정식 결제 가맹점 등록(D1 portone)에도 요구.
**Files**:
- 수정: `templates/settings.html` 한의원 정보 섹션
- 수정: `src/routers/clinic.py` (`POST /api/settings/clinic/business-registration` 업로드, `GET` 조회)
- 신규: `static/uploads/business/` (gitignore)
- 마이그레이션: `clinics` 테이블 — `business_reg_path TEXT`, `business_reg_number TEXT`, `business_reg_status TEXT DEFAULT 'none'`, `business_reg_uploaded_at`, `business_reg_verified_at`, `business_reg_rejection_reason`
**Critical edges**:
- 권한: chief_director 전용 (settings 다른 chief 항목과 동일 패턴, ai-lock-banner 참조)
- 파일 검증: 매직바이트 + content-type 이중 확인 (jpg/png/pdf만)
- 개인정보: 사업자등록번호 = 개인정보 → 처리방침 위탁업체 항목에 추가 검토
- 미인증 상태에서 결제 진입 차단 (D1 webhook 활성 시점부터)
**Effort**: CC ~1.5h.
**Depends on**: 없음 (베타 1 종료 후 시작).

---

### C2. 어드민 사업자정보 검토 패널
**What**: `templates/admin_clinics.html` 행 확장에 첨부파일 미리보기(이미지 inline / pdf 새 탭) + Verify·Reject 버튼 + 반려 사유 입력 모달.
**Why**: 사람이 직접 검토하지 않으면 위조 자동 통과. Cohort 2(25인)는 손으로 처리 가능 규모.
**Files**:
- 수정: `templates/admin_clinics.html`
- 수정: `src/routers/admin.py` (`POST /api/admin/clinics/{id}/business-reg/verify`, `/reject`)
- 알림: 검토 결과 이메일 자동 발송 (`plan_notify._send_smtp` 재사용)
**Critical edges**:
- 첨부 접근 권한: ADMIN_CLINIC_ID + chief_director **또는** ADMIN_SECRET (기존 어드민 가드 패턴 그대로)
- 반려 사유는 사용자에게 그대로 노출 → 사내 약어·내부 추측 금지 안내
- 검토 ledger: `business_reg_audit` 테이블 (verified_by, ts, action, reason)
**Effort**: CC ~1h.
**Depends on**: C1.

---

### C3. 휴대폰 본인인증 (PASS / KCP / NICE)
**What**: 회원가입 또는 한의원 등록 직후 본인인증 1회. 결과: `users.phone_verified`, `users.real_name_verified`, `users.ci_hash`(중복 가입 차단).
**Why**: 동일인 다중 한의원 등록 차단 = trial 어뷰저 1차 방어. 사업자등록증과 명의 일치 확인.
**Files**:
- 신규: `src/auth_phone.py` (PASS·KCP·NICE 중 1개 어댑터)
- 수정: `templates/onboard.html` 또는 `templates/settings.html` (가입 흐름 직후 step)
- 수정: `users` 테이블 마이그레이션 (`phone_verified`, `ci_hash UNIQUE`, `verified_at`)
**Critical edges**:
- CI(연계정보) 해시 — 본인 식별값. 평문 저장 금지, 단방향 해시만.
- 인증사 계약: PASS는 통신사 직거래, NICE/KCP는 대행. 비용 100~300원/건. **계약 리드타임 2~4주**.
- 회원가입과 분리: 가입 후 별도 step (인증사 외부 리다이렉트 → 콜백)
- 본인인증 미완료 = 베타 2 신청 자체 불가 (DB 레벨 차단)
**Effort**: CC ~2h + 인증사 계약 시간(외부).
**Depends on**: C1 (사업자등록 명의 대조 위해).

---

### C4. 결제 카드 사전 등록 (portone)
**What**: 베타 2 신청 시 결제 카드를 미리 등록(과금 X). 베타 2→정식 전환 시 자동 청구.
**Why**: 신용카드 = 어뷰저 비용 진입장벽 + 정식 전환 마찰 0. portone billing key 발급만.
**Files**:
- 신규: `src/payment_portone.py` (billing key 발급/저장/취소)
- 수정: `templates/settings.html` 또는 신규 `templates/billing.html`
- 마이그레이션: `subscriptions` 테이블 활성화 (현재 빈 셸) — `billing_key`, `card_last4`, `registered_at`, `next_charge_at`
**Critical edges**:
- portone 가맹점 계약 — **사업자등록증 필수**. C1 인증 완료 후 진행.
- billing key 저장은 portone 측. 우리 DB는 reference만.
- 베타 2 무료 약속 — 카드 등록 ≠ 즉시 과금. UI에 명확히 표시 + 약관 별도 동의.
- 정식 출시 30일 전 공지 의무 (terms.html 제8조).
**Effort**: CC ~3h + portone 계약 외부 시간.
**Depends on**: C1, C3 (사업자등록 + 명의 인증 둘 다 완료된 한의원만).

---

## Deferred (베타 런치 트랙 외, 2026-04-27 결정)

### D1. 결제 webhook (portone)
**Trigger**: 5인 베타 종료 후, 정식 오픈 직전.
**Why defer**: 베타는 무료가 정석. 사업자등록 / KCP 가맹점 / portone 계약 외부 의존성 많음. 시그널 약하면 매몰.
**Context**: `plan_guard.py` 한도 체크는 이미 작동. webhook 서명 검증 + 환불 처리 미구현.

---

### D2. PostgreSQL 마이그레이션
**Trigger**: 30인 wave 직전.
**Why defer**: 5인 베타엔 SQLite + 일일 백업으로 충분. 마이그 버그가 외부 베타 중 터지면 치명타. managed Postgres latency +30ms.
**Context**: Supabase 또는 Neon 무료 티어 후보. 자동 백업 포함.

---

### D3. Phase 2 기능 (CRM / 음성 차트 / 재고 / 스케줄 등)
**Trigger**: 30인 베타 설문·인터뷰 결과 분석 후.
**Why defer**: 추측 기반 빌드 금지 (사용자 명시). 30인 wave 데이터로 우선순위 결정.
**필수 수집 항목**:
1. 사용 경험 (어디서 막히는지)
2. 차후 필요 기능 선호도
**Context**: B5(피드백 시스템 강화)가 이 결정의 데이터 인프라.

---

## P1 (다음 구현)

### 5. [설정 페이지] 팀 & 권한 관리 UI
**What**: 직원 목록 테이블 + 모듈 권한 토글 + 역할 변경 UI
**Why**: 백엔드 API 완료. UI 연결만 필요.
**Context**: `/api/modules/config`, `/api/auth/invite` 이미 구현됨. `settings_setup.html` 초기 설정 위자드는 별도로 있음. 일반 설정 페이지(`settings.html`)에 팀 관리 탭 신규 생성 필요.
**Depends on**: 없음. 바로 시작 가능.

---

### 6. [설정 페이지] 한의원 프로필 ✅
**완료일**: 2026-04-21
**What**: 한의원 이름/주소/전화, 진료과목, 진료시간, 원장 소개글(블로그 자동 반영)
**Context**: clinics 테이블 ALTER 마이그레이션 완료. GET/POST /api/settings/clinic/profile 구현.
  설정 > 시스템 & 보안 탭 내 서브탭 구조로 구현 (chief_director 전용)

---

### 7. [설정 페이지] AI 설정 ✅
**완료일**: 2026-04-21
**What**: API Key 마스킹 표시/변경, 모델 선택 (Haiku/Sonnet/Opus), 월 예산 한도
**Context**: Fernet 암호화로 DB 저장. GET/POST /api/settings/clinic/ai 구현. chief_director 전용.
  블로그 생성 시 DB 저장 키 우선 사용은 별도 연결 필요 (현재는 .env 키 유지)

---

### 8. [설정 페이지] 블로그 설정 ✅
**완료일**: 2026-04-21
**What**: 질문단계 ON/OFF·개수, 글자 수, 기본 톤 선택, 프롬프트 직접 편집
**Context**: config.yaml 직접 수정 (save_blog_config). 프롬프트 편집은 chief_director 전용.
  GET/POST /api/settings/blog + /api/settings/blog/prompt 구현. 콘텐츠 에이전트 탭으로 배치.

---

### 9. 비밀번호 찾기 기능 ✅
**완료일**: 2026-04-21
**What**: 원장이 직원 편집 패널에서 "비밀번호 재설정 링크 생성" → 72시간 유효 링크 복사 → 직원이 링크로 비밀번호 재설정
**Context**: create_reinvite() 신규 함수 (기존 토큰 만료 처리 후 새 토큰 생성).
  complete_onboarding()이 기존 사용자는 INSERT 대신 UPDATE로 처리하도록 수정.
  POST /api/settings/staff/{id}/reinvite 엔드포인트 구현.

---

## P2 (설정 페이지 나머지)

### 10. [설정 페이지] 알림 설정
**What**: 재처방/예약취소/카카오톡 알림, 브라우저/이메일 방식 선택
**Context**: CRM 모듈 이전엔 알림 대상 없음. UI는 미리 만들되 기능은 빈 상태.
**Depends on**: CRM 모듈 시작 후

---

### 11. [설정 페이지] 보안
**What**: 비밀번호 변경 폼, 로그인 이력 조회 (최근 10건)
**Context**: 비밀번호 변경 API 이미 구현 (`/api/auth/change-password`). 로그인 이력 DB 테이블 추가 필요.
**Depends on**: DB 마이그레이션

---

### 12. [설정 페이지] 플랜 & 사용량
**What**: 현재 플랜 표시, 이번 달 토큰/비용, 월 예산 대비 프로그레스 바
**Context**: 토큰 추적 테이블 DB 추가 필요. blog_history.json 기반 간이 구현 가능.
**Depends on**: AI 설정 (월 예산 한도) 완료 후

---

### 13. [설정 페이지] 데이터 관리
**What**: 블로그 이력 CSV 내보내기, 직원 권한 초기화, 전체 데이터 삭제 (한의원명 입력 확인)
**Depends on**: 설정 페이지 기본 구조 완료 후

---

## P2 (기타)

### 14. 실제 한의원 블로그 10편 베타 테스트
**What**: 원장님 본인 한의원 블로그에 MVP 생성 글 10편 실제 업로드.
**Why**: 실제 사용 데이터가 Phase 2(휴머나이저 등) 진입 기준.
**Depends on**: FastAPI MVP 완성.

---

### 15. BYOAI 온보딩 위자드 ✅ (2026-04-22 — #15 대체)
**What**: 앱 내 4단계 위자드로 API 키 온보딩 진행. RBAC 분기(대표원장만 설정 가능), 실제 API 유효성 검증, ?onboard=1 자동 오픈.
**Supersedes**: BYOAI 온보딩 가이드 영상 (가이드 영상 불필요, 인앱 위자드로 대체)

---

### 15-a. API 키 자동 감지 (접두사 인식)
**What**: 붙여넣은 문자열에서 `sk-ant-` / `sk-proj-` / `AIza` 등 접두사를 인식해 공급사를 자동 판별. 위자드 Step 1 선택 없이 바로 Step 3으로 이동.
**Why**: 위자드 시나리오 선택 단계가 불필요해져서 더 빠른 온보딩 가능.
**Depends on**: Gemini/ChatGPT 멀티 제공자 지원 시 구현 (현재는 Claude 단독).

---

### 16. 휴머나이저 (AI 냄새 제거)
**What**: 생성된 블로그 글을 더 자연스러운 사람 문체로 변환.
**Depends on**: 베타 테스트 10편 결과로 필요성 판단.

---

### 17. JWT 인증 + API 키 암호화 (멀티유저)
**What**: 멀티유저 전환 시 필요한 인증 시스템 강화.
**Context**: 현재 로컬 전용. SaaS 공개 시 필수.
**Depends on**: 고객 수 증가 후.

---

### 18. 대시보드 중앙 현황판
**What**: 여러 기능(블로그, CRM, 재고 등)을 한 화면에서 확인.
**Depends on**: 2번째 기능(CRM 또는 재고) 완성 후.

---

### 19. 10개 AI 모델 어댑터 (GPT, Gemini 등)
**What**: Claude 외 다른 AI 모델도 선택 가능하게.
**Depends on**: 실제 고객 요청 발생 시.

---

## 미래 보안 강화

### 20. 이메일 인증 (Email Verification)
**What**: 회원 가입/온보딩 완료 시 이메일 인증 링크 발송 → 클릭 후 계정 활성화.
**Why**: 대부분의 서비스에서 이메일 인증을 통해 실제 이메일 소유자 확인 및 스팸 계정 방지.
**현재 상태**: 베타 초대 링크 자체가 이메일 수신 + 접속 확인 역할을 겸하므로 베타 기간에는 불필요.
**구현 시점 권장**:
  1. 30인 wave 이후 공개 셀프 가입(self-signup) 기능 추가 시
  2. 초대 없이 이메일/비밀번호로 직접 가입 가능한 흐름 추가 시
**구현 방안**:
  - `users.email_verified INTEGER DEFAULT 0` 컬럼 추가
  - 가입 시 `email_verify_token TEXT UNIQUE` 생성 → `/verify-email?token=` 링크 발송
  - 링크 클릭 시 `email_verified = 1` 업데이트
  - `plan_notify._send_smtp()` 헬퍼 이미 구현되어 있으므로 이메일 발송 로직 재사용 가능
  - 미인증 계정은 일부 기능 제한 (블로그 생성 등) 또는 72시간 내 인증 요구
**Depends on**: 공개 셀프 가입 기능 구현 시
