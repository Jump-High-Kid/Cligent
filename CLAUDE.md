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

## 현재 구현 상태 (2026-04-15 기준)

### 블로그 생성기 MVP (완성)

**첫 번째 기능 — 한의원 블로그 자동 생성기**

#### 폴더 구조
```
medical-assistant/
├── run.py              # 서버 시작 (python3 run.py)
├── conftest.py         # pytest 경로 설정
├── config.yaml         # 노코드 커스터마이징 (질문 수, 글 길이, 톤 등)
├── requirements.txt
├── .env                # ANTHROPIC_API_KEY 설정 (gitignore)
├── .env.example
├── prompts/
│   ├── questions.txt   # 질문 생성 프롬프트 (편집 가능)
│   └── blog.txt        # 블로그 생성 프롬프트 (편집 가능)
├── src/
│   ├── main.py         # FastAPI 앱
│   ├── config_loader.py
│   ├── question_generator.py
│   └── blog_generator.py
├── templates/
│   └── index.html      # 3단계 UI
└── tests/
    └── test_blog.py    # 7개 테스트 (전체 통과)
```

#### API 엔드포인트
- `GET /` — 블로그 생성기 UI
- `POST /questions` — 주제 기반 맞춤 질문 생성 (Claude 호출)
- `POST /generate` — 블로그 SSE 스트리밍 생성 (Claude 호출)

#### 3단계 플로우
1. 주제 입력
2. Claude가 생성한 질문 3개에 답변 (선택사항)
3. 실시간 스트리밍으로 블로그 생성 + 토큰/비용 표시

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
