"""
conftest.py — pytest가 src/ 폴더를 모듈 경로로 인식하도록 설정
"""
import sys
from pathlib import Path

# src/ 폴더를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent / "src"))
