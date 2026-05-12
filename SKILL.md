---
name: investment-agent
description: Autonomous investment agent with momentum inflection detection, market regime classification, and Telegram bot integration. Use when building or operating an AI-powered investment advisory system that generates daily reports, monitors positions, and delivers insights via Telegram.
---

# Investment Agent Skill

An autonomous investment advisory system that combines real-time market data collection, momentum inflection detection, market regime classification, and AI-powered analysis to deliver actionable investment insights via Telegram.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  Telegram Bot (bot.py)                                  │
│  ├── User commands (report, position, check)            │
│  └── Scheduled notifications                            │
├─────────────────────────────────────────────────────────┤
│  Report Engine (report_engine.py)                       │
│  ├── Collects all data in parallel                      │
│  └── Calls AI with enriched prompt                      │
├─────────────────────────────────────────────────────────┤
│  Analysis Engines                                       │
│  ├── momentum_inflection.py (6-phase detection)         │
│  ├── market_regime.py       (bull/bear/sideways)        │
│  └── entry_filter.py        (RSI/BB/Volume filter)      │
├─────────────────────────────────────────────────────────┤
│  Data Layer                                             │
│  ├── data_market.py   (indices, stocks, sectors)        │
│  ├── data_news.py     (RSS feeds, theme extraction)     │
│  ├── data_calendar.py (economic events)                 │
│  ├── data_yahoo.py    (deep stock insights)             │
│  └── database.py      (SQLite with WAL mode)            │
└─────────────────────────────────────────────────────────┘
```

## Core Concepts

### 1. Momentum Inflection Detection (6 Phases)

Tracks Rate of Change (ROC) and its derivative (acceleration):

| Phase | Condition | Signal |
|-------|-----------|--------|
| Accelerating Up | momentum > 0, slope > +0.3 | HOLD |
| Decelerating Up | momentum > 0, slope < -0.3 | PREPARE EXIT |
| Peak | momentum > 0, slope flips +→- | TAKE PROFIT |
| Accelerating Down | momentum < 0, slope < -0.3 | AVOID |
| Decelerating Down | momentum < 0, slope > +0.3 | PREPARE ENTRY |
| Trough | momentum < 0, slope flips -→+ | BUY |

### 2. Market Regime Classification

Composite score: KOSPI/NASDAQ trend + VIX + sector breadth + USD/KRW.
Regimes: **Bull** (>15) / **Correction** (0~15) / **Sideways** (-15~0) / **Bear** (<-15)

### 3. Entry Timing Filter

Prevents chasing overheated stocks:
- RSI (14-day): green 30-65 / yellow 65-75 / red >75
- Bollinger Band position
- Volume spike detection
- ADX trend strength

## Setup

```bash
git clone https://github.com/jungducknam/investment-agent-skill.git
cd investment-agent-skill
cp templates/config/.env.example .env
pip install -r requirements.txt
python run.py
```

## File Reference

Read `references/module-guide.md` for detailed module documentation.
Read `references/prompt-engineering.md` for AI prompt design patterns.
