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

The user prompt is a structured `REPORT_INPUT_JSON` object. In Telegram mode the local API client sends it to the configured model and expects strict JSON. In Agent command mode the prompt is returned to the outer agent, which should answer with its own model but must not override deterministic execution fields.

1. **metadata**: report date, timezone, prompt version, report type, execution policy, and strict JSON response format
2. **instructions**: facts/inferences/actions separation, use-only-provided-data, and `do_not_generate_execution_numbers`
3. **deterministic_layer**: market session status, report policy, market regime, verified/tentative events, event sections, and data quality
4. **market_data**: current index snapshots, sector momentum, and tracked stock universe
5. **technical_entry**: per-stock RSI, Bollinger Band position, ATR/support/resistance, and composite entry signal
6. **news**: selected report news, impact table, source links, affected assets, and theme news
7. **market_context**: flow summary, market memory, historical news, Yahoo insights, and extra user context
8. **output_schema**: the report JSON shape; execution price fields are nullable because the rule engine finalizes them later

## Output Format

The AI returns structured JSON containing market summary, candidate rationale, waiting list, rejected candidates, portfolio strategy, and watchlist. It must leave execution numbers null when they are not directly provided. After the model response, `recommendation_safety.py` calculates or validates executable prices, risk/reward, position size, evidence IDs, `risk_gate_status`, `action_status`, and `is_executable`.

Outer agents should produce Markdown from the returned payload/report, but prices, stops, targets, position sizes, and execution permissions must come from `price_engine_output`, `risk_gate_results`, or the deterministic report fields. If a field is absent, say it is unavailable instead of inventing it.

## Runtime Selection

Telegram mode currently uses `gemini-2.5-flash` for reports and smaller GPT-class models for chat/position review through an OpenAI-compatible API. Agent command mode does not select a model inside this repository; Codex, OpenAI Agents SDK, Manus, or another outer agent receives the prompt payload and chooses the model/runtime itself.
