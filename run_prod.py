"""
run_prod.py — 운영 서버 시작 스크립트 (launchd 호출용)

run.py 와의 차이:
  - reload=False (운영: 코드 변경 자동 반영 차단 → 안정성·성능)
  - log_level="info"

배포 후 재시작:
    launchctl kickstart -k gui/$UID/kr.cligent.app
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
        reload=False,
        log_level="info",
    )
