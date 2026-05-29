import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from src.calendar_verifier import build_event_display_list, build_event_sections, infer_report_type, verify_calendar_events
from src.config import KST
from src.market_regime_engine import calculate_market_regime
from src.market_session import build_market_session_status
from src.performance_tracker import record_recommendation_snapshots
from src.price_engine import calculate_position_size, validate_risk_reward
from src.recommendation_safety import apply_recommendation_safety_controls


class ExecutionLayerTest(unittest.TestCase):
    def test_calendar_string_events_are_tentative_not_verified(self):
        result = verify_calendar_events(
            {"economic_events": ["미국 CPI 발표"], "earnings": ["NVDA 실적발표: TBD"]},
            now_kst=KST.localize(datetime(2026, 5, 19, 1, 44)),
        )

        self.assertEqual(result["verified_events"], [])
        self.assertEqual(len(result["tentative_events"]), 2)
        self.assertFalse(result["tentative_events"][0]["verified"])

    def test_event_display_includes_high_signal_tentative_and_news_events(self):
        result = verify_calendar_events(
            {"economic_events": ["FOMC minutes release"], "earnings": ["NVDA earnings: TBD"]},
            now_kst=KST.localize(datetime(2026, 5, 20, 12, 0)),
        )

        display = build_event_display_list(
            result["verified_events"],
            result["tentative_events"],
            [{"title": "삼성전자 노조 파업 예정, 반도체 생산 차질 우려"}],
        )

        self.assertIn("FOMC 의사록 공개", display)
        self.assertIn("Nvidia 실적 발표 대기", display)
        self.assertIn("삼성전자 노조 파업 예정", display)

    def test_report_type_detects_us_intraday_from_kst_night(self):
        report_type = infer_report_type(KST.localize(datetime(2026, 5, 19, 1, 44)))

        self.assertEqual(report_type, "US_INTRADAY")

    def test_market_regime_sets_risk_off_budget(self):
        regime = calculate_market_regime({
            "indices": {
                "NASDAQ": {"change_pct": -2.1},
                "SP500": {"change_pct": -1.4},
                "KOSPI": {"change_pct": -1.2},
                "KOSDAQ": {"change_pct": -1.3},
                "VIX": {"price": 27},
                "USD_KRW": {"change_pct": 0.9},
                "BRENT": {"change_pct": 2.5},
            },
            "sector_mom": {"반도체(US)": {"ret_5d": -3.0}},
        })

        self.assertIn(regime["global_regime"], {"neutral_risk_off", "risk_off", "crisis"})
        self.assertLessEqual(regime["risk_budget"]["max_single_position_pct"], 2)
        self.assertGreaterEqual(regime["risk_budget"]["cash_min_pct"], 40)

    def test_risk_reward_validation_blocks_outliers(self):
        low = validate_risk_reward(100, 95, 104)
        high = validate_risk_reward(100, 99, 112)
        review = validate_risk_reward(100, 98, 111)

        self.assertEqual(low["status"], "FAIL")
        self.assertEqual(high["status"], "FAIL")
        self.assertEqual(review["status"], "REVIEW")

    def test_position_size_uses_stop_distance_and_regime_cap(self):
        size = calculate_position_size(100, 92, per_trade_risk_pct=0.25, max_position_pct=2)

        self.assertAlmostEqual(size, 2.0)

    def test_event_sections_split_this_week_next_major_and_schedule(self):
        now = KST.localize(datetime(2026, 5, 26, 7, 0))
        sections = build_event_sections(
            verified_events=[],
            tentative_events=[],
            news_items=[
                {
                    "title": "Is the stock market open on Memorial Day?",
                    "summary": "NYSE and Nasdaq reopen after the holiday.",
                    "primary_theme": "market_schedule",
                }
            ],
            market_session_status=build_market_session_status(now),
            now_kst=now,
        )

        self.assertIn("05/28 미국 PCE 물가", sections["this_week"])
        self.assertIn("06/10 미국 CPI", sections["next_major"])
        self.assertIn("06/16~17 FOMC", sections["next_major"])
        self.assertTrue(any("Memorial Day" in item for item in sections["market_schedule"]))

    def test_high_per_growth_stock_is_rate_sensitive_conditional(self):
        report, ctx = _base_us_recommendation_context(include_news=True)
        ctx["yahoo_insights"] = {"PLTR": {"per": 150.0}}
        ctx["indices"] = {"US10Y": {"price": 4.56, "change_pct": 0.02}}

        apply_recommendation_safety_controls(report, ctx)

        rec = report["recommendations"][0]
        self.assertFalse(rec["is_executable"])
        self.assertEqual(rec["action_status"], "conditional_entry")
        self.assertIn("high_valuation_rate_sensitive", rec["execution_blockers"])

    def test_executable_candidate_without_news_catalyst_is_conditional(self):
        report, ctx = _base_us_recommendation_context(include_news=False)
        ctx["indices"] = {"US10Y": {"price": 4.0, "change_pct": -0.01}}

        apply_recommendation_safety_controls(report, ctx)

        rec = report["recommendations"][0]
        self.assertFalse(rec["is_executable"])
        self.assertEqual(rec["action_status"], "conditional_entry")
        self.assertIn("missing_catalyst_evidence", rec["execution_blockers"])

    def test_performance_snapshot_save_is_idempotent(self):
        report = {
            "_meta": {"collected_at": "2026-05-19T01:44:00+09:00"},
            "market_regime": {"global_regime": "neutral_risk_off"},
            "recommendations": [
                {
                    "rank": 1,
                    "ticker": "NVDA",
                    "name": "NVIDIA",
                    "market": "US",
                    "currency": "USD",
                    "action_status": "conditional_entry",
                    "is_executable": False,
                    "risk_gate_status": "PASS_WITH_CONDITIONS",
                    "entry_price": 218.5,
                    "current_price": 221.0,
                    "target_price_1": 235.0,
                    "target_price_2": 248.0,
                    "stop_loss": 207.8,
                    "position_size_pct": 1.5,
                    "risk_reward_1": 1.55,
                    "confidence_score": 67,
                    "evidence_ids": ["news_001", "price_nvda"],
                }
            ],
        }
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "snapshots.db"
            first = record_recommendation_snapshots("20260519", report, db_path=db_path)
            second = record_recommendation_snapshots("20260519", report, db_path=db_path)

            self.assertEqual(first, 1)
            self.assertEqual(second, 1)

    def test_performance_snapshot_tolerates_string_candidates(self):
        report = {
            "_meta": {"collected_at": "2026-05-22T07:00:00+09:00"},
            "recommendations": [],
            "waiting_list": ["NVDA"],
            "rejected_candidates": ["005930.KS"],
        }
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "snapshots.db"
            inserted = record_recommendation_snapshots("20260522", report, db_path=db_path)

            self.assertEqual(inserted, 2)


def _base_us_recommendation_context(include_news: bool) -> tuple[dict, dict]:
    report = {
        "recommendations": [
            {
                "ticker": "PLTR",
                "name": "Palantir",
                "market": "US",
                "currency": "USD",
                "style": "스윙",
                "confidence_score": 72,
                "investment_rationale": ["AI 데이터 수요가 견조하다."],
            }
        ],
    }
    ctx = {
        "collected_at": KST.localize(datetime(2026, 5, 27, 7, 0)).isoformat(),
        "stock_prices": {"PLTR": {"price": 137.0, "source": "Yahoo", "name": "Palantir", "market": "US", "volume": 1000000}},
        "entry_signals": {
            "PLTR": {
                "current_price": 137.0,
                "signal": "적정",
                "rsi": 55.0,
                "bb_position": 50.0,
                "atr_14": 6.0,
                "support_20d": 129.0,
                "resistance_20d": 153.0,
                "volume_signal": "normal",
                "reasons": ["RSI 중립", "BB 중심선 부근"],
            }
        },
    }
    if include_news:
        ctx["detailed_news"] = [{"title": "Palantir AI demand remains in focus", "source": "Reuters_Biz"}]
    return report, ctx


if __name__ == "__main__":
    unittest.main()
