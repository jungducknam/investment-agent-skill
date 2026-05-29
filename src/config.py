"""
config.py — 환경변수 기반 설정 관리
OCI 인스턴스에서 .env 파일 또는 환경변수로 설정 주입
"""
import os
from pathlib import Path

# ── 기본 경로 ─────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── 텔레그램 ─────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Manus API (AI 호출용) ────────────────────────────
MANUS_API_KEY = os.getenv("MANUS_API_KEY", "")
MANUS_API_BASE = os.getenv("MANUS_API_BASE", "https://api.manus.im/api/llm-proxy/v1")

# ── OpenAI 호환 (Manus proxy 또는 직접) ──────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", MANUS_API_BASE)

# ── Google Gemini API (리포트 직접 호출용) ────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_MODEL_REPORT = os.getenv("GEMINI_MODEL_REPORT", "gemini-3.5-flash")
GEMINI_REPORT_THINKING_LEVEL = os.getenv("GEMINI_REPORT_THINKING_LEVEL", "low")
GEMINI_REPORT_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_REPORT_MAX_OUTPUT_TOKENS", "16000"))

# ── DeepL 번역 API (뉴스 표시용) ───────────────────────
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
DEEPL_API_URL = os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2/translate")
DEEPL_TARGET_LANG = os.getenv("DEEPL_TARGET_LANG", "KO")

# ── 한국투자증권 Open API (국장 시세용) ───────────────
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")

# ── AI 모델 설정 ─────────────────────────────────────
MODEL_REPORT = os.getenv("MODEL_REPORT", "gemini-2.5-flash")
MODEL_POSITION = os.getenv("MODEL_POSITION", "gpt-4.1-nano")
MODEL_CHAT = os.getenv("MODEL_CHAT", "gpt-4.1-mini")

# ── DB ────────────────────────────────────────────────
DB_PATH = DATA_DIR / "positions.db"

# ── 모니터링 설정 ────────────────────────────────────
ALERT_THRESHOLD_PCT = float(os.getenv("ALERT_THRESHOLD_PCT", "5.0"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "10"))

# ── 타임존 ───────────────────────────────────────────
import pytz
KST = pytz.timezone("Asia/Seoul")
ET = pytz.timezone("America/New_York")
