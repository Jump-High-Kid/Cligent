"""
영상 촬영용 데모 계정 생성 스크립트
실행: python3 scripts/create_demo_account.py

매 촬영 전 실행하면 새 계정 + 온보딩 URL을 발급해 줍니다.
"""
import sqlite3
import secrets
import sys
import os
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'cligent.db')
BASE_URL = os.getenv('BASE_URL', 'https://cligent.kr')


def create_demo_account():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. 기존 demo 계정 정리 (이메일 패턴: demo+숫자@cligent.kr)
    cur.execute("DELETE FROM beta_applicants WHERE email LIKE 'demo+%@cligent.kr'")
    cur.execute("DELETE FROM users WHERE email LIKE 'demo+%@cligent.kr'")
    # 영상촬영용 클리닉 usage_logs 초기화
    cur.execute("""
        DELETE FROM usage_logs WHERE clinic_id IN (
            SELECT id FROM clinics WHERE name LIKE '%영상촬영%'
        )
    """)
    # 영상촬영용 클리닉 삭제 (clinic_id=1 제외)
    cur.execute("DELETE FROM clinics WHERE name LIKE '%영상촬영%' AND id != 1")

    # 2. 새 클리닉 생성
    trial_expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    cur.execute("""
        INSERT INTO clinics (name, max_slots, plan_id, trial_expires_at)
        VALUES (?, 5, 'free', ?)
    """, ('영상촬영용 한의원', trial_expires))
    clinic_id = cur.lastrowid

    # 3. 타임스탬프 기반 이메일 (중복 방지)
    ts = datetime.now().strftime('%m%d%H%M')
    email = f'demo+{ts}@cligent.kr'
    token = secrets.token_urlsafe(32)

    # 4. beta_applicants 등록
    cur.execute("""
        INSERT INTO beta_applicants
            (name, clinic_name, phone, email, note, invite_token, status, invited_at)
        VALUES (?, ?, ?, ?, ?, ?, 'invited', ?)
    """, (
        '데모원장',
        '영상촬영용 한의원',
        '010-0000-0000',
        email,
        '영상촬영용 임시 계정',
        token,
        datetime.now(timezone.utc).isoformat(),
    ))

    # 5. invites 테이블에도 등록 (onboard 엔드포인트가 여기서 검증)
    expires = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
    cur.execute("""
        INSERT INTO invites (clinic_id, email, role, token, expires_at, created_by, created_at)
        VALUES (?, ?, 'chief_director', ?, ?, 1, ?)
    """, (clinic_id, email, token, expires, datetime.now(timezone.utc).isoformat()))

    conn.commit()
    conn.close()

    onboard_url = f'{BASE_URL}/onboard?token={token}'

    print()
    print('=' * 55)
    print('  영상 촬영용 데모 계정 생성 완료')
    print('=' * 55)
    print(f'  이메일   : {email}')
    print(f'  비밀번호 : 촬영 시 직접 설정')
    print(f'  클리닉   : 영상촬영용 한의원 (id={clinic_id})')
    print()
    print('  온보딩 URL (브라우저에 붙여넣기):')
    print(f'  {onboard_url}')
    print('=' * 55)
    print()


if __name__ == '__main__':
    create_demo_account()
