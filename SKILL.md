---
name: investment-agent
description: Use when the user invokes /investment-skill, $investment-skill, /investment-agent, or $investment-agent, or asks for daily investment reports, position reviews, entry timing checks, or Korean/US market analysis through Codex or another agent runtime.
---

# Investment Agent Skill

This skill supports two deployment styles. Pick one based on the user's intent.

## Command Invocation

Treat `/investment-skill` as the primary explicit invocation alias. The text after the command is the user request. Also support `$investment-skill`, `/investment-agent`, and `$investment-agent` as aliases.

Default to Mode B for command-style invocations unless the user explicitly asks to run a Telegram bot, background service, scheduler, server, or deployment.

Command routing:

- `/investment-skill 금일 리포트 작성`, `/investment-skill 오늘 리포트`, `/investment-skill daily report`: run `scripts/build_agent_request.py report`.
- `/investment-skill 오늘 반도체 섹터 어때?`, stock/sector/macro questions, or general investment questions: run `scripts/build_agent_request.py chat "<request without the command prefix>"`.
- `/investment-skill 포지션 점검 ...`: if the user provides complete position JSON or enough fields to build it, run `scripts/build_agent_request.py position --position-json '<json>'`; otherwise ask for the missing ticker, market, entry price, quantity, direction, and current price if needed.

When using Mode B, read the returned JSON payload and use its `system_prompt`, `user_prompt`, and `data` as context for the final answer. The payload is an internal handoff format, not the user-facing result. Do not expose the raw payload unless the user asks for it.

For report requests, produce a human-readable Korean Markdown report. Do not answer with raw JSON. Include a one-line conclusion, market overview, sector/theme summary, entry candidates, wait/do-not-chase candidates, portfolio strategy, key risks, data-quality notes, and an investment disclaimer.

## Mode A: Standalone Telegram System

Use this when the user wants a 24/7 bot server.

- Requires `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and an OpenAI-compatible `OPENAI_API_KEY`.
- Runs `python run.py` or the systemd service template.
- Telegram is the UI. The bot collects data, calls the configured AI API, stores reports/positions locally, and sends replies or alerts.
- Entry point: `src/bot.py`.
- AI API entry point: `src/ai_client.py`.

Setup:

```bash
cp templates/config/.env.example .env
pip install -r requirements.txt
python run.py
```

## Mode B: On-Demand Agent Command

Use this when Codex, OpenAI Agents SDK, Manus, or another agent runtime should act as the AI.

- Does not require Telegram credentials.
- Does not start a long-running scheduler or background automation.
- Builds market context and prompts only when the user asks.
- The outer agent should read the returned `system_prompt` and `user_prompt`, then answer with its own model.
- Entry point: `src/agent_adapter.py`.
- CLI helper: `scripts/build_agent_request.py`.

Examples:

```bash
scripts/build_agent_request.py report
scripts/build_agent_request.py chat "오늘 반도체 섹터 어때?"
scripts/build_agent_request.py position --position-json '{"id":1,"name":"NVIDIA","ticker":"NVDA","market":"US","entry_price":100,"quantity":2,"currency":"USD","direction":"long"}' --current-price 112
```

Python:

```python
from src.agent_adapter import build_report_request

request = build_report_request()
# Send request["system_prompt"] and request["user_prompt"] to the outer agent model.
```

## Core Analysis Modules

- `src/report_engine.py`: collects on-demand market data and builds reusable report prompts.
- `src/momentum_inflection.py`: detects momentum phase and inflection quality.
- `src/market_regime.py`: classifies Korean and US market regimes.
- `src/entry_filter.py`: checks RSI, Bollinger Bands, volume, and ADX for entry timing.
- `src/position_tracker.py`: parses and evaluates positions.

This skill intentionally excludes periodic market-memory storage and scheduled momentum archiving. It is a lightweight on-demand analysis package, while Telegram mode remains available for users who want an independent bot.

Read `references/module-guide.md` for module details and `references/prompt-engineering.md` for prompt rules.
