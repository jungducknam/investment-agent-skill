"""
news_report_selector.py — choose report-worthy news after classification.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def select_report_news(news_items: list[dict[str, Any]], max_items: int = 8) -> list[dict[str, Any]]:
    selected = []
    theme_count = defaultdict(int)
    source_count = defaultdict(int)

    ranked = sorted(news_items or [], key=lambda item: item.get("impact_score", 0), reverse=True)
    for item in ranked:
        if item.get("validation_status") == "FAIL":
            continue
        if item.get("report_priority") == "exclude":
            continue
        if float(item.get("impact_score", 0) or 0) < 65:
            continue
        theme = item.get("primary_theme") or "unknown"
        source = item.get("source") or "unknown"
        if theme_count[theme] >= 2:
            continue
        if source_count[source] >= 3:
            continue
        if theme in {"company_specific", "consumer"} and not _has_direct_universe_asset(item):
            continue
        if theme in {"irrelevant_market_news", "market_schedule"}:
            continue

        selected.append(item)
        theme_count[theme] += 1
        source_count[source] += 1
        if len(selected) >= max_items:
            break
    return selected


def _has_direct_universe_asset(item: dict[str, Any]) -> bool:
    for asset in item.get("affected_assets") or []:
        if asset.get("impact_scope") == "direct" and asset.get("asset_type") == "ticker":
            return True
    return False
