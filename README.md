# 📈 Investment Agent Skill

[English](#english) | [한국어](#korean)

---

<a name="english"></a>
## 🇬🇧 English

An investment advisory system that combines real-time market data collection, momentum inflection detection, market regime classification, and AI-ready prompt generation. It supports both a standalone Telegram bot and an on-demand Agent AI workflow.

### 🌟 Key Features

| Feature | Description |
|---------|-------------|
| **Momentum Inflection Engine** | Detects 6 phases of momentum using Rate of Change (ROC) derivatives to identify peaks and troughs before they happen |
| **Market Regime Classification** | Automatically classifies the market into Bull, Correction, Sideways, or Bear and adjusts strategy accordingly |
| **Entry Timing Filter** | Uses RSI, Bollinger Bands, Volume, and ADX to flag stocks as 🟢 Optimal, 🟡 Wait, or 🔴 Overheated |
| **AI-Powered Reports** | Generates structured investment reports with entry/target/stop prices using enriched market context |
| **Position Monitoring** | Tracks your positions in real-time and sends alerts when targets or stop-losses are hit |
| **Telegram Integration** | Delivers daily briefings, alerts, and on-demand reports through a standalone Telegram bot |
| **Agent Command Mode** | Builds prompts and data payloads for Codex, OpenAI Agents SDK, Manus, or another agent to answer on demand |

### 🏗 Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Telegram Bot (bot.py)                                  │
│  ├── User commands (report, position, check)            │
│  └── Scheduled notifications                            │
├─────────────────────────────────────────────────────────┤
│  Report Engine (report_engine.py)                       │
│  ├── Parallel data collection (ThreadPoolExecutor)      │
│  └── AI prompt construction + JSON parsing              │
├─────────────────────────────────────────────────────────┤
│  Analysis Engines                                       │
│  ├── momentum_inflection.py (6-phase detection)         │
│  ├── market_regime.py       (bull/bear/sideways)        │
│  └── entry_filter.py        (RSI/BB/Volume/ADX)        │
├─────────────────────────────────────────────────────────┤
│  Data Layer                                             │
│  ├── data_market.py   (indices, stocks, sectors)        │
│  ├── data_news.py     (RSS feeds, theme extraction)     │
│  ├── data_calendar.py (economic events)                 │
│  ├── data_yahoo.py    (deep stock insights)             │
│  └── database.py      (SQLite with WAL mode)            │
└─────────────────────────────────────────────────────────┘
```

### 🚀 Quick Start

#### Option A — Standalone Telegram Bot

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
cp templates/config/.env.example .env
# Edit .env with your Telegram Bot Token and OpenAI API Key
pip install -r requirements.txt
python run.py
```

#### Option B — On-Demand Agent AI

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
pip install -r requirements.txt

# Build a report request for the outer agent model.
scripts/build_agent_request.py report

# Build a free-form investment question request.
scripts/build_agent_request.py chat "How does the semiconductor sector look today?"
```

Agent mode does not require `TELEGRAM_BOT_TOKEN` or a long-running bot process. It creates the market context and prompts only when the user issues a command.

### ⚙️ Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather | Telegram mode only |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID | Telegram mode only |
| `OPENAI_API_KEY` | OpenAI-compatible API key | Telegram mode only |
| `OPENAI_BASE_URL` | Custom API endpoint | Optional |

### 🔧 Deployment (Systemd)

For 24/7 operation on a Linux server:

```bash
sudo cp templates/config/investment-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now investment-bot
```

### 📖 How the Momentum Inflection Engine Works

The engine tracks not just price direction, but the **acceleration of momentum** — the rate at which momentum itself is changing. This allows it to detect peaks and troughs before they become obvious.

**6 Phases of Momentum:**

| Phase | What It Means | Action |
|-------|---------------|--------|
| 🚀 Accelerating Up | Momentum is getting stronger | Hold position |
| ⚠️ Decelerating Up | Still rising, but slowing down | Prepare to exit |
| 🔴 Peak | Momentum has peaked and is turning | Take profit |
| 📉 Accelerating Down | Falling faster and faster | Stay away |
| 🔵 Decelerating Down | Still falling, but slowing | Prepare to buy |
| 🟢 Trough | Momentum has bottomed and is turning | Buy signal |

### 📚 Documentation

| Document | Description |
|----------|-------------|
| [Module Guide](references/module-guide.md) | Detailed documentation for each module |
| [Prompt Engineering](references/prompt-engineering.md) | AI prompt design patterns and rules |

### 📄 License

MIT License. See [LICENSE](LICENSE) for details.

---

<a name="korean"></a>
## 🇰🇷 한국어

실시간 시장 데이터 수집, 모멘텀 변곡점 감지, 시장 레짐 분류, 그리고 AI용 프롬프트 생성을 결합한 투자 에이전트 시스템입니다. 독립형 텔레그램 봇 방식과 에이전트 AI 명령형 방식을 모두 지원합니다.

### 🌟 핵심 기능

| 기능 | 설명 |
|------|------|
| **모멘텀 변곡 판단 엔진** | 변화율(ROC)의 2차 미분을 분석하여 모멘텀을 6단계로 분류, 정점과 저점을 사전에 감지합니다 |
| **시장 레짐 분류** | 시장을 강세/조정/횡보/약세 4단계로 자동 분류하고 전략을 조정합니다 |
| **진입 타이밍 필터** | RSI, 볼린저밴드, 거래량, ADX를 종합하여 🟢 적정 / 🟡 대기 / 🔴 과열 판별 |
| **AI 리포트 생성** | 풍부한 시장 컨텍스트를 기반으로 진입가/목표가/손절가가 포함된 구조화된 리포트 생성 |
| **포지션 모니터링** | 등록된 포지션을 실시간 추적하여 목표가/손절가 도달 시 알림 |
| **텔레그램 연동** | 매일 아침 브리핑, 장중 알림, 즉각적인 리포트를 텔레그램으로 전달 |
| **에이전트 명령형 모드** | Codex, OpenAI Agents SDK, Manus 등 외부 에이전트가 명령 시점에 사용할 데이터와 프롬프트 생성 |

### 🏗 아키텍처

이 시스템은 CPU를 거의 사용하지 않는 `asyncio` 스케줄러 기반으로 24시간 동작하며, 리포트 생성 시 `ThreadPoolExecutor`를 통해 모든 데이터를 병렬로 수집합니다.

| 모듈 | 역할 |
|------|------|
| `bot.py` | 텔레그램 인터페이스 및 백그라운드 루프 관리 |
| `report_engine.py` | AI 프롬프트 조합 및 JSON 리포트 생성 |
| `momentum_inflection.py` | 6단계 모멘텀 변곡 감지 |
| `market_regime.py` | 시장 레짐 분류 (강세/조정/횡보/약세) |
| `entry_filter.py` | 종목 진입 타이밍 필터 (RSI/BB/거래량/ADX) |
| `data_market.py` | 주요 지수, 종목 가격, 섹터 모멘텀 수집 |
| `data_news.py` | 뉴스 RSS 수집 및 테마별 분류 |
| `data_calendar.py` | 경제 이벤트 캘린더 |
| `data_yahoo.py` | 애널리스트 목표가, 재무 지표 |

### 🚀 시작하기

#### 방식 A — 텔레그램 + API 키 독립 시스템

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
cp templates/config/.env.example .env
# .env 파일에 텔레그램 봇 토큰과 OpenAI API 키를 입력하세요
pip install -r requirements.txt
python run.py
```

#### 방식 B — 에이전트 AI 스킬 명령형

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
pip install -r requirements.txt

# 외부 에이전트가 읽을 리포트 요청 JSON 생성
scripts/build_agent_request.py report

# 자유 질문 요청 JSON 생성
scripts/build_agent_request.py chat "오늘 반도체 섹터 어때?"
```

에이전트 명령형 방식은 텔레그램 토큰이나 상시 실행 봇이 필요 없습니다. 사용자가 명령할 때만 시장 데이터를 수집하고, 외부 에이전트가 자기 모델로 답변할 수 있는 `system_prompt`와 `user_prompt`를 만듭니다.

### 📖 모멘텀 변곡 판단 엔진 작동 원리

이 엔진은 단순 가격 방향이 아닌 **모멘텀의 가속도** — 모멘텀 자체가 변하는 속도 — 를 추적합니다. 이를 통해 정점과 저점이 명확해지기 전에 미리 감지할 수 있습니다.

**6단계 모멘텀 분류:**

| 단계 | 의미 | 시그널 |
|------|------|--------|
| 🚀 가속상승 | 모멘텀이 점점 강해지는 중 | 보유 |
| ⚠️ 감속상승 | 아직 오르지만 속도가 줄고 있음 | 이익실현 준비 |
| 🔴 정점 | 모멘텀이 최고점에서 꺾임 | 이익실현 |
| 📉 가속하락 | 점점 빠르게 하락 중 | 관망 |
| 🔵 감속하락 | 아직 빠지지만 속도가 줄고 있음 | 매수 준비 |
| 🟢 저점 | 모멘텀이 바닥에서 반등 시작 | 매수 |

### 📚 문서

| 문서 | 설명 |
|------|------|
| [모듈 가이드](references/module-guide.md) | 각 모듈별 상세 문서 |
| [프롬프트 엔지니어링](references/prompt-engineering.md) | AI 프롬프트 설계 패턴 및 규칙 |

### 📄 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE)를 참조하세요.
