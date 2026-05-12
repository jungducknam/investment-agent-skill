#!/bin/bash
# deploy.sh — OCI 인스턴스 배포 스크립트
# 사용법: bash scripts/deploy.sh

set -e

echo "=== Investment Bot 배포 ==="

# 1. 의존성 설치
echo "📦 의존성 설치..."
pip3 install -r requirements.txt

# 2. .env 확인
if [ ! -f .env ]; then
    echo "⚠️ .env 파일이 없습니다. .env.example을 복사하여 설정하세요:"
    echo "   cp .env.example .env"
    echo "   nano .env"
    exit 1
fi

# 3. 데이터 디렉토리 생성
mkdir -p data

# 4. 기존 프로세스 종료
pkill -f "python.*run.py" 2>/dev/null || true
sleep 2

# 5. 봇 실행 (백그라운드 + 로그)
echo "🚀 봇 시작..."
nohup python3 run.py > data/bot_stdout.log 2>&1 &
BOT_PID=$!
echo "✅ 봇 시작됨 (PID: $BOT_PID)"
echo $BOT_PID > data/bot.pid

# 6. 상태 확인
sleep 3
if kill -0 $BOT_PID 2>/dev/null; then
    echo "✅ 봇 정상 실행 중"
    tail -5 data/bot.log 2>/dev/null || tail -5 data/bot_stdout.log
else
    echo "❌ 봇 시작 실패"
    tail -20 data/bot_stdout.log
    exit 1
fi

echo ""
echo "=== 배포 완료 ==="
echo "로그 확인: tail -f data/bot.log"
echo "프로세스 확인: ps aux | grep run.py"
echo "종료: kill \$(cat data/bot.pid)"
