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

## 기술 스택

> 프로젝트 초기화 시 결정 예정. 아래 권장 스택 참고.

- **백엔드**: Python (FastAPI) 또는 Node.js (Express/Fastify)
- **프론트엔드**: React + TypeScript 또는 Flutter (모바일 대응)
- **DB**: PostgreSQL (환자 기록) + Redis (캐시)
- **AI/ML**: Claude API (anthropic SDK) — 변증(辨證) 보조, 처방 추천

## 개발 시작 시 확인 사항

1. `.env` 파일에 필요한 환경변수 설정 (`.env.example` 참고)
2. 환자 데이터를 다루는 경우 암호화 설정 확인
3. Claude API 사용 시 `claude-api` 스킬 참고

## 주의사항

- 환자 식별 정보(이름, 주민번호, 연락처)는 로그에 출력 금지
- 처방 로직은 반드시 의료진 최종 확인 단계 포함
- 의료 기록 삭제는 소프트 딜리트(soft delete) 방식 사용
