# Prompt Engineering Guide

## System Prompt Design

The AI receives a carefully structured system prompt with enforced rules that shape its investment recommendations.

### Rule 1: Anti-Chase Protection

Momentum strength does not equal a buy signal. Strong momentum only indicates direction; entry timing requires separate technical confirmation. Stocks with RSI above 75 or trading above the upper Bollinger Band are flagged as "overheated" and excluded from buy recommendations. This is the single most important rule — it prevents the classic mistake of recommending stocks that have already rallied significantly.

### Rule 2: Regime-Aware Allocation

Cash reserve recommendations scale with the detected market regime. In a Bull market, the system suggests 5-10% cash. During Correction phases, 15-25% cash is recommended. Sideways markets call for 20-30% cash, and Bear markets require 30-50% cash reserves. This ensures the AI never recommends full allocation during uncertain conditions.

### Rule 3: Entry Signal Enforcement

The entry timing filter produces three signals that the AI must respect. A green (Optimal) signal allows confident recommendations. A yellow (Wait) signal restricts recommendations to conditional entries only, such as "buy on pullback to price X." A red (Overheated) signal forces the stock to be excluded from recommendations entirely or moved to a watching list.

### Rule 4: Independent Market Judgment

Korean and US markets are judged independently. US sector momentum is not directly applied to Korean stock recommendations, and vice versa. Each market has its own regime score, and cross-market correlations are noted but not used as direct trading signals.

## User Prompt Structure

The user prompt is constructed from collected data in the following order. In Telegram mode the local API client sends it to the configured model. In Agent command mode the prompt is returned to the outer agent, which should answer with its own model.

1. **Market Indices**: Current prices with change percentages for all tracked indices (KOSPI, KOSDAQ, S&P500, NASDAQ, VIX, USD/KRW, US10Y, Brent, Gold)
2. **Entry Timing Signals**: Per-stock RSI, Bollinger Band position, and composite signal
3. **Sector Momentum**: Sector ETF performance rankings (5-day and 20-day)
4. **Headlines**: Top 8-10 market-moving news items
5. **Theme News**: News grouped by investment theme (semiconductor, AI, defense, energy, etc.)
6. **Economic Events**: Upcoming events and earnings dates
7. **Stock Universe**: Tracked stocks with current prices and technical data
8. **Yahoo Finance Insights**: Analyst targets and financial metrics
9. **Output Instructions**: JSON format specification for structured response

## Output Format

The AI returns structured JSON containing a market summary with overall sentiment and regime assessment, ranked stock recommendations with entry/target/stop prices and confidence levels, a waiting list of overheated stocks to monitor for pullback opportunities, and portfolio strategy guidance based on the current regime.

## Runtime Selection

Telegram mode currently uses `gemini-2.5-flash` for reports and smaller GPT-class models for chat/position review through an OpenAI-compatible API. Agent command mode does not select a model inside this repository; Codex, OpenAI Agents SDK, Manus, or another outer agent receives the prompt payload and chooses the model/runtime itself.
