"""
recommendation_validator.py — Pydantic validation for final report shape.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    name: str | None = None
    risk_gate_status: str
    action_status: str
    is_executable: bool
    price_source: str
    evidence_ids: list[str]
    risk_reward_1: float
    position_size_pct: float


class ReportSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_date: str | None = None
    market_summary: dict[str, Any] = Field(default_factory=dict)
    portfolio_strategy: dict[str, Any] = Field(default_factory=dict)
    recommendations: list[Recommendation] = Field(default_factory=list)
    waiting_list: list[dict[str, Any]] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


def validate_report_schema(report: dict) -> None:
    try:
        ReportSchema.model_validate(report)
    except ValidationError as exc:
        raise ValueError(f"report schema validation failed: {exc}") from exc


def briefing_only_report(report: dict, error: Exception | str) -> dict:
    safe = dict(report or {})
    safe["recommendations"] = []
    safe["waiting_list"] = list(safe.get("waiting_list") or [])
    safe["rejected_candidates"] = list(safe.get("rejected_candidates") or [])
    safe["briefing_only"] = True
    safe["validation_error"] = str(error)
    safe.setdefault("market_summary", {})
    safe.setdefault("portfolio_strategy", {})
    return safe
