#!/usr/bin/env python3
"""
run.py — Investment Bot 엔트리포인트
.env 파일 로드 후 봇 실행
"""
import sys
from pathlib import Path

# 프로젝트 루트
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# .env 파일 로드
try:
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✅ .env 로드 완료: {env_path}")
    else:
        print("⚠️ .env 파일 없음 — 환경변수에서 설정을 읽습니다.")
except ImportError:
    print("⚠️ python-dotenv 미설치 — 환경변수에서 직접 읽습니다.")

# 봇 실행
from src.bot import main

if __name__ == "__main__":
    main()
