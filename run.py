"""
run.py — 개발 서버 시작 스크립트
실행: python run.py
접속: http://localhost:8000
"""
import sys
from pathlib import Path

# src/ 폴더를 Python 경로에 추가 (uvicorn이 모듈을 찾을 수 있도록)
sys.path.insert(0, str(Path(__file__).parent / "src"))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,          # 코드 변경 시 자동 재시작
        reload_dirs=["src"],  # src/ 변경만 감시
    )
