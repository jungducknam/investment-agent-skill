# Module Guide

## Core Modules

### bot.py — Telegram Bot Main

The entry point for the Telegram bot, built on `python-telegram-bot` (v20+) with a fully async architecture using `ApplicationBuilder`. It handles user commands (`/start`, inline keyboard buttons), manages background monitoring loops via `asyncio.create_task`, and uses `drop_pending_updates=True` to prevent message floods after restarts. The httpx/httpcore log levels are suppressed to WARNING to avoid token leakage in logs.

### agent_adapter.py — On-Demand Agent Interface

Builds request payloads for Codex, OpenAI Agents SDK, Manus, or another outer agent. It does not start Telegram, does not require Telegram credentials, and does not call an AI API directly. Instead it returns `system_prompt`, `user_prompt`, and supporting data so the outer agent can answer with its own model when the user issues a command.

### report_engine.py — Report Generation Harness

The report engine collects market data from all sources in parallel using `ThreadPoolExecutor`, enriches it with market session status, verified/tentative events, market regime, data-quality scores, news impact scores, market memory, and historical news context, then builds one structured `REPORT_INPUT_JSON` prompt. Telegram mode calls the configured AI model and parses strict JSON. Agent mode reuses the same prompt builder and lets the outer agent produce a readable answer while respecting deterministic execution fields.

### price_engine.py / risk_gate.py / recommendation_safety.py — Deterministic Execution Layer

LLM output is treated as candidate commentary. `price_engine.py` calculates entry, stop, target, risk/reward, and position size from current price, ATR, support/resistance, and regime budget. `risk_gate.py` blocks price-missing, overbought, poor risk/reward, stale-data, event-risk, and unsupported catalyst cases. `recommendation_safety.py` applies those calculations to the final report and demotes unsafe candidates to waiting or rejected lists.

### momentum_inflection.py — 6-Phase Momentum Detection

This module calculates the Rate of Change (ROC) over 10 periods, applies 3-period EMA smoothing, then computes the slope (acceleration/deceleration) of the smoothed momentum. It detects sign flips in the slope to identify inflection points and classifies each stock/sector into one of 6 phases with confidence scores. The same logic applies at both individual stock and sector levels for rotation detection.

### market_regime_engine.py — Market Regime Classification

Computes a composite score from index moves, VIX, USD/KRW, rates, oil, and sector momentum. The result includes Korean/US regimes, a global regime, risk score, and risk budget caps used by the deterministic execution layer.

### entry_filter.py — Entry Timing Filter

Calculates RSI (14-day), Bollinger Band position (20-day, 2 sigma), volume spike detection (vs 20-day average), and ADX (14-day) trend strength. These are combined into a composite signal: 🟢 Optimal (safe to enter), 🟡 Wait (conditional entry only), or 🔴 Overheated (exclude from recommendations).

## Data Modules

### data_market.py — Market Data Collection

Fetches major indices via KIS where configured and yfinance fallback, individual stock prices with change percentages, session/staleness annotations, and sector ETF performance over 5-day and 20-day windows.

### data_quality_engine.py — Data Quality Scoring

Scores each market data item for missing price, stale timestamps, source conflicts, generic sources, missing technical signals, and regular-session confirmation requirements. The report prompt exposes this table so weak data is treated as a risk, not a trade signal.

### data_news.py — News Collection

Aggregates RSS feeds from multiple Korean and English sources, maps news articles to specific tickers via keyword matching, groups articles by investment themes (semiconductor, AI, defense, energy, etc.), and extracts top headlines for the report prompt.

### data_calendar.py — Economic Calendar

Collects upcoming economic events (FOMC, employment data, CPI, etc.) and earnings dates, filtered by relevance to tracked markets.

### data_yahoo.py — Yahoo Finance Deep Insights

Retrieves analyst recommendations, consensus target prices, key financial metrics (P/E, revenue growth, margins), and formats them for injection into the AI prompt.

### database.py — SQLite Management

Manages the local SQLite database with WAL mode enabled for concurrent read/write access. Auto-creates required tables on first run and provides thread-safe connection handling for position tracking and report storage.

## Support Modules

### ai_client.py — AI API Client

The sole module that makes external AI API calls in Telegram mode. It also exposes prompt builders so Agent mode can reuse the same instructions without calling the configured API key.

### monitor.py — Position Monitoring

Runs as an asyncio background task that checks registered positions against current market prices. Sends Telegram alerts when target prices or stop-losses are reached. Uses adaptive intervals: frequent checking during market open, reduced frequency during off-hours.

### position_tracker.py — Position Management

Handles CRUD operations for investment positions (long/short, ticker, entry price, quantity, target, stop-loss). Integrates with the database module for persistence.

### report_formatter.py — Message Formatting

Converts structured JSON report data into formatted Telegram messages with mobile-readable action sections, explicit waiting/rejected candidates, news impact summaries, event sections, data-quality notes, and position-management context.

### config.py — Configuration

Loads all configuration from environment variables. Defines the KST timezone constant, stock universe, and monitoring intervals. No secrets are hardcoded.
