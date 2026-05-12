# Investment Agent Skill

[English](#english) | [한국어](#korean)

---

<a name="english"></a>
## English

Investment Agent Skill is a lightweight investment-analysis package for Korean and US markets. It combines real-time market data collection, momentum inflection detection, market regime classification, entry timing filters, and AI-ready prompts.

It supports two usage modes:

1. **Standalone Telegram System**: a 24/7 bot that uses Telegram credentials and an OpenAI-compatible API key.
2. **On-Demand Agent Command Mode**: a skill/tool package that Codex, Manus, OpenAI Agents SDK, or another agent runtime uses only when the user explicitly asks for an analysis.

This repository intentionally excludes scheduled market-memory storage and periodic momentum archiving. It is designed to stay lightweight and command-driven, while still supporting Telegram for users who want an independent bot.

### Core Features

| Feature | What it does |
| --- | --- |
| Momentum inflection engine | Classifies momentum into 6 phases using ROC and acceleration/deceleration. |
| Market regime classifier | Classifies Korean and US markets into bull, correction, sideways, or bear regimes. |
| Entry timing filter | Uses RSI, Bollinger Bands, volume, and ADX to avoid chasing overheated stocks. |
| Report prompt builder | Builds structured report prompts with market data, news, calendar, Yahoo insights, and entry signals. |
| Position review | Reviews registered or supplied positions with rule-based and AI-ready judgment context. |
| Telegram adapter | Runs as an independent Telegram bot with API-key backed AI calls. |
| Agent adapter | Returns `system_prompt`, `user_prompt`, and data payloads for an outer agent to answer. |

### Choosing a Mode

| Use case | Recommended mode |
| --- | --- |
| You want a server that keeps running and replies in Telegram. | Standalone Telegram System |
| You want alerts and position monitoring without opening Codex each time. | Standalone Telegram System |
| You want Codex/Manus/another agent to generate analysis only when you ask. | On-Demand Agent Command Mode |
| You do not want to store Telegram credentials or run a long-lived bot. | On-Demand Agent Command Mode |
| You want the outer agent model to act as the final investment analyst. | On-Demand Agent Command Mode |

## Mode A: Standalone Telegram System

### Purpose

Use this mode when you want an independent service. The bot runs on a server, listens to Telegram messages, collects market data, calls the configured AI API, and sends reports or position checks back to Telegram.

### Requirements

| Requirement | Notes |
| --- | --- |
| Python 3.11+ | Recommended for server deployment. |
| Telegram bot token | Create one with BotFather. |
| Telegram chat ID | Used for replies and notifications. |
| OpenAI-compatible API key | OpenAI, Manus proxy, or another compatible endpoint. |
| Linux server for 24/7 use | Optional, but recommended for systemd deployment. |

### Environment Variables

Create `.env` from the template:

```bash
cp templates/config/.env.example .env
```

Required for Telegram mode:

```bash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
OPENAI_API_KEY=your_openai_compatible_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

Optional model settings:

```bash
MODEL_REPORT=gemini-2.5-flash
MODEL_POSITION=gpt-4.1-nano
MODEL_CHAT=gpt-4.1-mini
```

### Local Run

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
cp templates/config/.env.example .env
pip install -r requirements.txt
python run.py
```

### Server Deployment With Systemd

```bash
sudo cp templates/config/investment-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now investment-bot
sudo systemctl status investment-bot
```

### Telegram Flow

1. User sends a Telegram command or presses a button.
2. `src/bot.py` routes the request.
3. Market data is collected from `src/data_market.py`, `src/data_news.py`, `src/data_calendar.py`, and `src/data_yahoo.py`.
4. `src/report_engine.py` builds the report context.
5. `src/ai_client.py` calls the configured AI API.
6. The formatted answer is sent back through Telegram.

### Telegram Mode Commands

| Action | How it is handled |
| --- | --- |
| Daily report | Generates or loads a report and sends Telegram-formatted messages. |
| Position list | Reads saved positions and calculates current PnL. |
| Add position | Parses text such as `삼성전자 70000 10` or `short AMD 140 5`. |
| Quick check | Forces AI-assisted review of active positions. |
| Free question | Sends the user's question to the configured chat model with investment instructions. |

## Mode B: On-Demand Agent Command Mode

### Purpose

Use this mode when the AI runtime is outside this repository. Codex, Manus, OpenAI Agents SDK, or another agent calls this package only when the user asks for analysis. The package does not start Telegram, does not run a scheduler, and does not need Telegram credentials.

In this mode, the repository acts as a data and prompt builder. The outer agent receives a machine-readable payload, but the final user-facing answer must be a human-readable Markdown report, not raw JSON.

### Requirements

| Requirement | Notes |
| --- | --- |
| Python 3.11+ | Needed to run the helper scripts. |
| Market data dependencies | Installed from `requirements.txt`. |
| Telegram credentials | Not required. |
| API key inside this repository | Not required if the outer agent supplies the model. |

### Quick Start

Install the repository first:

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
pip install -r requirements.txt
```

You can now use it in two ways.

#### Option 1: Use the CLI directly

Build a report request from the repository:

```bash
scripts/build_agent_request.py report
```

This prints a JSON payload for an outer agent. The payload is not the final user-facing answer; the outer agent should turn it into a readable Markdown report.

#### Option 2: Attach it as a local Codex skill

Copy the repository into Codex's local skills directory:

```bash
mkdir -p ~/.codex/skills/investment-agent
rsync -a --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'data' \
  --exclude 'agent-report-request-*.json' \
  --exclude 'agent-report-request-*.summary.md' \
  ./ ~/.codex/skills/investment-agent/
```

Then restart Codex so it reloads the skill list.

To update the installed Codex skill after pulling new changes:

```bash
cd investment-agent-skill
git pull origin master
rsync -a --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'data' \
  --exclude 'agent-report-request-*.json' \
  --exclude 'agent-report-request-*.summary.md' \
  ./ ~/.codex/skills/investment-agent/
```

To remove it from Codex:

```bash
rm -rf ~/.codex/skills/investment-agent
```

Restart Codex after installing, updating, or removing the skill.

### Command Reference

Use the skill-style command phrase in Codex or another agent runtime:

```text
/investment-skill 금일 리포트 작성
/investment-skill 오늘 반도체 섹터 어때?
/investment-skill 포지션 점검 NVIDIA 100달러 2주 보유
```

The repository treats `/investment-skill` as the primary explicit trigger phrase in `SKILL.md`. It also supports `$investment-skill`, `/investment-agent`, and `$investment-agent` as aliases.

| Command | Purpose |
| --- | --- |
| `/investment-skill 오늘 리포트` | Collects market data and builds a report request. Final answer should be readable Korean Markdown. |
| `/investment-skill 금일 리포트 작성` | Same as today's report. |
| `/investment-skill daily report` | Same as today's report. |
| `/investment-skill 오늘 반도체 섹터 어때?` | Handles a stock, sector, macro, or market question. |
| `/investment-skill NVDA 지금 진입해도 돼?` | Handles a single-stock entry or risk question. |
| `/investment-skill 포지션 점검 NVDA 진입가 100 현재가 112 수량 2 롱` | Builds a position review request. |

Equivalent aliases:

```text
$investment-skill 오늘 리포트
/investment-agent 오늘 리포트
$investment-agent 오늘 리포트
```

### Manual CLI Commands

Build a report request without live data collection:

```bash
scripts/build_agent_request.py report --no-collect
```

Build a free-form investment question:

```bash
scripts/build_agent_request.py chat "How does the semiconductor sector look today?"
```

Build a position review request:

```bash
scripts/build_agent_request.py position \
  --position-json '{"id":1,"name":"NVIDIA","ticker":"NVDA","market":"US","entry_price":100,"quantity":2,"currency":"USD","direction":"long"}' \
  --current-price 112
```

### Agent Payload Shape

The command returns JSON because agents and tools need a structured handoff format:

```json
{
  "mode": "agent",
  "task": "investment_report",
  "system_prompt": "...",
  "user_prompt": "...",
  "data": {},
  "expected_output": "..."
}
```

The outer agent should:

1. Read `system_prompt`.
2. Read `user_prompt`.
3. Use `data` as supporting context if needed.
4. Generate the final report, chat answer, or position review with its own model.
5. Show the user a readable Markdown answer. Do not show the raw JSON payload unless the user explicitly asks for it.

For `investment_report`, the generated prompt explicitly asks for a Korean Markdown report with:

- one-line conclusion
- market overview
- sector/theme summary
- entry candidates
- wait/do-not-chase candidates
- portfolio strategy
- key risks
- data-quality notes
- investment disclaimer

### Python Usage

```python
from src.agent_adapter import (
    build_report_request,
    build_chat_request,
    build_position_review_request,
)

report_request = build_report_request()
chat_request = build_chat_request("How does the power infrastructure sector look today?")
```

### OpenAI Agents SDK Usage

This repository does not require `openai-agents` by default. If your runtime uses the OpenAI Agents SDK, install it in that runtime and expose the optional tools:

```python
from agents import Agent
from src.agent_adapter import get_openai_agent_tools

agent = Agent(
    name="Investment Analyst",
    instructions="Use the investment tools to build context, then answer with risk-managed analysis.",
    tools=get_openai_agent_tools(),
)
```

### Agent Mode Flow

1. User asks the outer agent for a report, position review, or market question.
2. The outer agent calls `scripts/build_agent_request.py` or functions in `src/agent_adapter.py`.
3. The package collects data if needed and builds prompts.
4. The outer agent uses its own model to produce the final readable Markdown answer.
5. No Telegram process, bot token, or long-running service is involved.

## Architecture

```text
investment-agent-skill/
├── run.py                         # Telegram mode entry point
├── scripts/build_agent_request.py # Agent command mode CLI
├── src/
│   ├── bot.py                     # Telegram adapter
│   ├── agent_adapter.py           # Agent command adapter
│   ├── ai_client.py               # API client for Telegram mode and shared prompt builders
│   ├── report_engine.py           # Data collection and report prompt builder
│   ├── entry_filter.py            # RSI/BB/volume/ADX entry timing
│   ├── market_regime.py           # Market regime classification
│   ├── momentum_inflection.py     # Momentum phase detection
│   ├── position_tracker.py        # Position parsing and rule-based review
│   └── data_*.py                  # Market, news, calendar, Yahoo data
├── references/
│   ├── module-guide.md
│   └── prompt-engineering.md
└── templates/config/
    ├── .env.example
    └── investment-bot.service
```

## Notes and Safety

- This project provides investment analysis support, not financial advice.
- Final investment decisions remain the user's responsibility.
- Market data can be delayed, incomplete, or unavailable depending on Yahoo Finance and RSS sources.
- Agent mode is best when a human or outer agent is actively requesting analysis.
- Telegram mode is best when the user wants an independent service that keeps running.

## Documentation

| Document | Description |
| --- | --- |
| [Module Guide](references/module-guide.md) | Module responsibilities and integration notes. |
| [Prompt Engineering](references/prompt-engineering.md) | Prompt rules for anti-chase, regime-aware allocation, and output format. |

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<a name="korean"></a>
## 한국어

Investment Agent Skill은 한국과 미국 시장을 분석하기 위한 경량 투자 분석 패키지입니다. 실시간 시장 데이터 수집, 모멘텀 변곡 판단, 시장 레짐 분류, 진입 타이밍 필터, AI용 프롬프트 생성을 결합합니다.

이 저장소는 두 가지 사용 방식을 지원합니다.

1. **텔레그램 + API 키 독립 시스템**: 서버에서 24시간 실행되는 텔레그램 봇 방식입니다.
2. **에이전트 AI 스킬 명령형 방식**: Codex, Manus, OpenAI Agents SDK 같은 외부 에이전트가 사용자의 명령 시점에만 분석 요청을 만들고 답변하는 방식입니다.

이 저장소는 정기 시장 기억 저장, 주기적 모멘텀 아카이빙, 장기 스케줄러를 의도적으로 제외합니다. 텔레그램 방식은 독립 실행을 지원하고, 에이전트 방식은 사용자가 명령할 때만 가볍게 동작하도록 설계했습니다.

### 핵심 기능

| 기능 | 설명 |
| --- | --- |
| 모멘텀 변곡 판단 | ROC와 가속/감속을 기반으로 모멘텀을 6단계로 분류합니다. |
| 시장 레짐 분류 | 한국과 미국 시장을 강세, 조정, 횡보, 약세로 분류합니다. |
| 진입 타이밍 필터 | RSI, 볼린저밴드, 거래량, ADX로 과열 종목 추격매수를 줄입니다. |
| 리포트 프롬프트 생성 | 시장 데이터, 뉴스, 일정, Yahoo 인사이트, 진입 시그널을 묶어 구조화된 프롬프트를 만듭니다. |
| 포지션 리뷰 | 저장된 포지션 또는 전달받은 포지션을 규칙 기반 및 AI용 컨텍스트로 점검합니다. |
| 텔레그램 어댑터 | API 키를 사용해 독립형 텔레그램 봇으로 실행합니다. |
| 에이전트 어댑터 | 외부 에이전트가 사용할 `system_prompt`, `user_prompt`, 데이터 payload를 반환합니다. |

### 어떤 모드를 선택해야 하나

| 상황 | 권장 방식 |
| --- | --- |
| 서버에서 계속 실행되며 텔레그램으로 응답하는 봇이 필요하다. | 텔레그램 + API 키 독립 시스템 |
| 포지션 알림과 장중 체크를 Codex를 열지 않아도 받고 싶다. | 텔레그램 + API 키 독립 시스템 |
| Codex, Manus, 다른 에이전트에게 명령할 때만 리포트를 만들고 싶다. | 에이전트 AI 스킬 명령형 방식 |
| 텔레그램 토큰을 저장하거나 장기 실행 프로세스를 두고 싶지 않다. | 에이전트 AI 스킬 명령형 방식 |
| 최종 판단을 외부 에이전트 모델이 직접 하게 하고 싶다. | 에이전트 AI 스킬 명령형 방식 |

## 방식 A: 텔레그램 + API 키 독립 시스템

### 목적

이 방식은 독립 서비스가 필요할 때 사용합니다. 봇이 서버에서 실행되며 텔레그램 메시지를 받고, 시장 데이터를 수집하고, 설정된 AI API를 호출한 뒤 리포트나 포지션 점검 결과를 텔레그램으로 보내줍니다.

### 필요 조건

| 필요 조건 | 설명 |
| --- | --- |
| Python 3.11 이상 | 서버 배포 기준으로 권장합니다. |
| 텔레그램 봇 토큰 | BotFather에서 생성합니다. |
| 텔레그램 chat ID | 답변과 알림 발송에 사용합니다. |
| OpenAI 호환 API 키 | OpenAI, Manus proxy, 기타 호환 endpoint를 사용할 수 있습니다. |
| Linux 서버 | 24시간 실행하려면 systemd 배포를 권장합니다. |

### 환경변수

템플릿에서 `.env`를 만듭니다.

```bash
cp templates/config/.env.example .env
```

텔레그램 방식 필수값:

```bash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
OPENAI_API_KEY=your_openai_compatible_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

선택 모델 설정:

```bash
MODEL_REPORT=gemini-2.5-flash
MODEL_POSITION=gpt-4.1-nano
MODEL_CHAT=gpt-4.1-mini
```

### 로컬 실행

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
cp templates/config/.env.example .env
pip install -r requirements.txt
python run.py
```

### 서버 배포

```bash
sudo cp templates/config/investment-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now investment-bot
sudo systemctl status investment-bot
```

### 텔레그램 방식의 동작 흐름

1. 사용자가 텔레그램에서 버튼을 누르거나 메시지를 보냅니다.
2. `src/bot.py`가 요청을 라우팅합니다.
3. `src/data_market.py`, `src/data_news.py`, `src/data_calendar.py`, `src/data_yahoo.py`가 시장 데이터를 수집합니다.
4. `src/report_engine.py`가 리포트 컨텍스트를 구성합니다.
5. `src/ai_client.py`가 설정된 AI API를 호출합니다.
6. 포맷팅된 답변이 텔레그램으로 전송됩니다.

### 텔레그램 주요 기능

| 기능 | 처리 방식 |
| --- | --- |
| 오늘 리포트 | 리포트를 생성하거나 캐시에서 불러와 텔레그램 메시지로 보냅니다. |
| 포지션 현황 | 저장된 포지션을 읽고 현재 손익을 계산합니다. |
| 포지션 등록 | `삼성전자 70000 10`, `short AMD 140 5` 같은 입력을 파싱합니다. |
| 즉시 체크 | 활성 포지션에 대해 AI 보조 판단을 강제로 수행합니다. |
| 자유 질문 | 사용자의 질문을 투자 분석 지침과 함께 설정된 chat 모델로 보냅니다. |

## 방식 B: 에이전트 AI 스킬 명령형 방식

### 목적

이 방식은 실제 AI 런타임이 저장소 밖에 있을 때 사용합니다. Codex, Manus, OpenAI Agents SDK, 다른 에이전트가 사용자의 명령이 들어온 순간 이 패키지를 호출합니다. 이 방식은 텔레그램을 시작하지 않고, 스케줄러도 돌리지 않으며, 텔레그램 토큰도 필요 없습니다.

이 저장소는 데이터와 프롬프트를 만드는 역할만 합니다. 내부 전달은 JSON payload로 하지만, 사용자가 보는 최종 리포트나 답변은 외부 에이전트가 사람이 읽을 수 있는 Markdown 텍스트로 생성해야 합니다.

### 필요 조건

| 필요 조건 | 설명 |
| --- | --- |
| Python 3.11 이상 | 헬퍼 스크립트 실행에 필요합니다. |
| 시장 데이터 의존성 | `requirements.txt`로 설치합니다. |
| 텔레그램 인증정보 | 필요 없습니다. |
| 저장소 내부 API 키 | 외부 에이전트가 모델을 제공한다면 필요 없습니다. |

### 빠른 시작

먼저 저장소를 설치합니다.

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
pip install -r requirements.txt
```

설치 후에는 두 가지 방식으로 사용할 수 있습니다.

#### 선택지 1: CLI를 직접 사용

저장소에서 바로 리포트 요청을 생성합니다.

```bash
scripts/build_agent_request.py report
```

이 명령은 외부 에이전트가 읽을 JSON payload를 출력합니다. 이 JSON은 최종 사용자 답변이 아닙니다. 외부 에이전트가 이 payload를 읽고 사람이 읽을 수 있는 Markdown 리포트로 바꿔야 합니다.

#### 선택지 2: Codex 로컬 스킬로 붙이기

저장소 내용을 Codex 로컬 스킬 디렉터리에 복사합니다.

```bash
mkdir -p ~/.codex/skills/investment-agent
rsync -a --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'data' \
  --exclude 'agent-report-request-*.json' \
  --exclude 'agent-report-request-*.summary.md' \
  ./ ~/.codex/skills/investment-agent/
```

그 다음 Codex를 재시작해야 스킬 목록이 다시 로드됩니다.

저장소를 최신화한 뒤 설치된 Codex 스킬도 업데이트하려면:

```bash
cd investment-agent-skill
git pull origin master
rsync -a --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'data' \
  --exclude 'agent-report-request-*.json' \
  --exclude 'agent-report-request-*.summary.md' \
  ./ ~/.codex/skills/investment-agent/
```

Codex에서 제거하려면:

```bash
rm -rf ~/.codex/skills/investment-agent
```

설치, 업데이트, 삭제 후에는 Codex를 재시작하세요.

### 명령어 목록

Codex나 다른 에이전트 런타임에서는 스킬식 명령 문구로 호출할 수 있습니다.

```text
/investment-skill 금일 리포트 작성
/investment-skill 오늘 반도체 섹터 어때?
/investment-skill 포지션 점검 NVIDIA 100달러 2주 보유
```

이 저장소의 `SKILL.md`는 `/investment-skill`을 primary 명시적 트리거 문구로 취급합니다. `$investment-skill`, `/investment-agent`, `$investment-agent`도 alias로 지원합니다.

| 명령어 | 용도 |
| --- | --- |
| `/investment-skill 오늘 리포트` | 시장 데이터를 수집하고 오늘 리포트 요청을 만듭니다. 최종 답변은 사람이 읽는 한국어 Markdown이어야 합니다. |
| `/investment-skill 금일 리포트 작성` | 오늘 리포트와 같은 의미입니다. |
| `/investment-skill daily report` | 오늘 리포트와 같은 의미입니다. |
| `/investment-skill 오늘 반도체 섹터 어때?` | 종목, 섹터, 매크로, 시장 질문으로 처리합니다. |
| `/investment-skill NVDA 지금 진입해도 돼?` | 단일 종목 진입/리스크 질문으로 처리합니다. |
| `/investment-skill 포지션 점검 NVDA 진입가 100 현재가 112 수량 2 롱` | 포지션 리뷰 요청을 만듭니다. |

동일한 alias 예시:

```text
$investment-skill 오늘 리포트
/investment-agent 오늘 리포트
$investment-agent 오늘 리포트
```

### 수동 CLI 명령어

실시간 데이터 수집 없이 프롬프트 껍데기만 생성:

```bash
scripts/build_agent_request.py report --no-collect
```

자유 질문 요청 생성:

```bash
scripts/build_agent_request.py chat "오늘 반도체 섹터 어때?"
```

포지션 리뷰 요청 생성:

```bash
scripts/build_agent_request.py position \
  --position-json '{"id":1,"name":"NVIDIA","ticker":"NVDA","market":"US","entry_price":100,"quantity":2,"currency":"USD","direction":"long"}' \
  --current-price 112
```

### 에이전트 payload 구조

명령은 JSON을 반환합니다. 이 JSON은 사람이 읽으라는 결과물이 아니라, 에이전트와 도구 사이의 구조화된 전달 형식입니다.

```json
{
  "mode": "agent",
  "task": "investment_report",
  "system_prompt": "...",
  "user_prompt": "...",
  "data": {},
  "expected_output": "..."
}
```

외부 에이전트는 다음 순서로 사용하면 됩니다.

1. `system_prompt`를 읽습니다.
2. `user_prompt`를 읽습니다.
3. 필요하면 `data`를 근거 데이터로 사용합니다.
4. 자기 모델로 최종 리포트, 자유 답변, 포지션 리뷰를 생성합니다.
5. 사용자에게는 원시 JSON이 아니라 사람이 읽을 수 있는 Markdown 답변을 보여줍니다.

`investment_report` 요청의 프롬프트는 최종 출력을 한국어 Markdown 리포트로 만들도록 지시합니다. 포함해야 하는 섹션은 다음과 같습니다.

- 한 줄 결론
- 시장 현황
- 섹터/테마 요약
- 진입 가능 후보
- 대기/추격 금지 후보
- 포트폴리오 전략
- 주요 리스크
- 데이터 품질 주의
- 투자 판단 책임 면책

### Python 사용 예시

```python
from src.agent_adapter import (
    build_report_request,
    build_chat_request,
    build_position_review_request,
)

report_request = build_report_request()
chat_request = build_chat_request("오늘 전력 인프라 섹터 어때?")
```

### OpenAI Agents SDK 예시

이 저장소는 `openai-agents`를 기본 의존성으로 강제하지 않습니다. OpenAI Agents SDK 런타임에서 사용하려면 해당 환경에 SDK를 설치하고 선택 도구를 노출하면 됩니다.

```python
from agents import Agent
from src.agent_adapter import get_openai_agent_tools

agent = Agent(
    name="Investment Analyst",
    instructions="Use the investment tools to build context, then answer with risk-managed analysis.",
    tools=get_openai_agent_tools(),
)
```

### 에이전트 방식의 동작 흐름

1. 사용자가 외부 에이전트에게 리포트, 포지션 리뷰, 시장 질문을 요청합니다.
2. 외부 에이전트가 `scripts/build_agent_request.py` 또는 `src/agent_adapter.py` 함수를 호출합니다.
3. 이 패키지가 필요한 경우 시장 데이터를 수집하고 프롬프트를 만듭니다.
4. 외부 에이전트가 자기 모델로 사람이 읽을 수 있는 Markdown 답변을 생성합니다.
5. 텔레그램 프로세스, 봇 토큰, 장기 실행 서비스는 사용하지 않습니다.

## 아키텍처

```text
investment-agent-skill/
├── run.py                         # 텔레그램 방식 진입점
├── scripts/build_agent_request.py # 에이전트 명령형 CLI
├── src/
│   ├── bot.py                     # 텔레그램 어댑터
│   ├── agent_adapter.py           # 에이전트 명령형 어댑터
│   ├── ai_client.py               # 텔레그램 방식 API 클라이언트 및 공용 프롬프트 빌더
│   ├── report_engine.py           # 데이터 수집 및 리포트 프롬프트 생성
│   ├── entry_filter.py            # RSI/BB/거래량/ADX 진입 타이밍
│   ├── market_regime.py           # 시장 레짐 분류
│   ├── momentum_inflection.py     # 모멘텀 단계 판단
│   ├── position_tracker.py        # 포지션 파싱 및 규칙 기반 리뷰
│   └── data_*.py                  # 시장, 뉴스, 일정, Yahoo 데이터
├── references/
│   ├── module-guide.md
│   └── prompt-engineering.md
└── templates/config/
    ├── .env.example
    └── investment-bot.service
```

## 주의 사항

- 이 프로젝트는 투자 분석 보조 도구이며 투자 자문을 대체하지 않습니다.
- 최종 투자 판단과 책임은 사용자에게 있습니다.
- Yahoo Finance와 RSS 데이터는 지연되거나 누락될 수 있습니다.
- 에이전트 방식은 사용자가 능동적으로 분석을 요청할 때 적합합니다.
- 텔레그램 방식은 독립적으로 계속 실행되는 서비스를 원할 때 적합합니다.

## 문서

| 문서 | 설명 |
| --- | --- |
| [Module Guide](references/module-guide.md) | 모듈 역할과 통합 방식 설명 |
| [Prompt Engineering](references/prompt-engineering.md) | 추격매수 방지, 레짐 기반 배분, 출력 포맷 규칙 |

## 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE)를 참조하세요.
