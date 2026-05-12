# Module Guide

## Core Modules

### bot.py — Telegram Bot Main

The entry point for the Telegram bot, built on `python-telegram-bot` (v20+) with a fully async architecture using `ApplicationBuilder`. It handles user commands (`/start`, inline keyboard buttons), manages background monitoring loops via `asyncio.create_task`, and uses `drop_pending_updates=True` to prevent message floods after restarts. The httpx/httpcore log levels are suppressed to WARNING to avoid token leakage in logs.

### report_engine.py — Report Generation

The report engine collects market data from all sources in parallel using `ThreadPoolExecutor` (5 workers), then constructs an enriched prompt containing index prices, sector momentum, news themes, economic calendar, Yahoo Finance insights, and entry timing signals. It calls the AI model (gemini-2.5-flash by default) with a structured system prompt that enforces anti-chase rules and regime-aware allocation, then parses the JSON response for formatting.

### momentum_inflection.py — 6-Phase Momentum Detection

This module calculates the Rate of Change (ROC) over 10 periods, applies 3-period EMA smoothing, then computes the slope (acceleration/deceleration) of the smoothed momentum. It detects sign flips in the slope to identify inflection points and classifies each stock/sector into one of 6 phases with confidence scores. The same logic applies at both individual stock and sector levels for rotation detection.

### market_regime.py — Market Regime Classification

Computes a composite score from multiple indicators including price vs 20-day moving average, VIX level and direction, sector breadth (advancing vs declining), and USD/KRW trend. The score maps to 4 regimes: Bull (>15), Correction (0~15), Sideways (-15~0), and Bear (<-15). Korean and US markets are scored independently.

### entry_filter.py — Entry Timing Filter

Calculates RSI (14-day), Bollinger Band position (20-day, 2 sigma), volume spike detection (vs 20-day average), and ADX (14-day) trend strength. These are combined into a composite signal: 🟢 Optimal (safe to enter), 🟡 Wait (conditional entry only), or 🔴 Overheated (exclude from recommendations).

## Data Modules

### data_market.py — Market Data Collection

Fetches major indices via yfinance (KOSPI, KOSDAQ, S&P500, NASDAQ, VIX, USD/KRW, US10Y, Brent, Gold), individual stock prices with change percentages, and sector ETF performance over 5-day and 20-day windows.

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

The sole module that makes external AI API calls. Provides functions for report generation, position judgment, and general Q&A. Designed to be model-agnostic — switching models requires only changing the model parameter.

### monitor.py — Position Monitoring

Runs as an asyncio background task that checks registered positions against current market prices. Sends Telegram alerts when target prices or stop-losses are reached. Uses adaptive intervals: frequent checking during market open, reduced frequency during off-hours.

### position_tracker.py — Position Management

Handles CRUD operations for investment positions (long/short, ticker, entry price, quantity, target, stop-loss). Integrates with the database module for persistence.

### report_formatter.py — Message Formatting

Converts structured JSON report data into formatted Telegram messages with proper emoji, sections, and inline keyboard buttons.

### config.py — Configuration

Loads all configuration from environment variables. Defines the KST timezone constant, stock universe, and monitoring intervals. No secrets are hardcoded.
