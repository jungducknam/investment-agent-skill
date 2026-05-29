import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.news_classifier import classify_news_item
from src.news_event_store import ensure_news_event_tables, save_news_events
from src.news_event_study import find_similar_event_summary
from src.news_impact_engine import calculate_news_impact
from src.news_report_selector import select_report_news
from src import snapshot_collector


class NewsIntelligenceTest(unittest.TestCase):
    def test_oil_news_is_energy_not_ai_or_semiconductor(self):
        item = classify_news_item({
            "title": "The oil market is reaching a tipping point as Brent jumps",
            "summary": "Higher oil prices may revive inflation pressure, rate concerns, and weigh on tech and AI stocks.",
            "source": "MarketWatch",
            "link": "https://example.com/oil",
        })

        self.assertEqual(item["primary_theme"], "energy_inflation")
        self.assertIn("rates_policy", item["secondary_themes"])
        self.assertNotIn("ai_infrastructure", [item["primary_theme"], *item["secondary_themes"]])
        self.assertNotIn("semiconductor", [item["primary_theme"], *item["secondary_themes"]])
        self.assertEqual(item["validation_status"], "PASS")
        self.assertIn("oil_news_not_ai", item["why_not_other_themes"])
        assets = {asset["asset"]: asset for asset in item["affected_assets"]}
        self.assertEqual(assets["BRENT"]["direction"], "positive")
        self.assertEqual(assets["NASDAQ"]["direction"], "negative")
        self.assertEqual(assets["NASDAQ"]["impact_scope"], "indirect")

    def test_crude_edges_lower_does_not_mark_brent_positive(self):
        item = classify_news_item({
            "title": "Crude oil edges lower as traders await clarity on U.S.-Iran talks",
            "summary": "The market still watches geopolitical supply risk.",
            "source": "MarketWatch",
        })

        assets = {asset["asset"]: asset for asset in item["affected_assets"]}
        self.assertEqual(item["primary_theme"], "energy_inflation")
        self.assertIn(assets["BRENT"]["direction"], {"negative", "neutral"})
        self.assertNotEqual(assets["BRENT"]["direction"], "positive")
        self.assertEqual(item["oil_direction"]["short_term_label"], "하락")
        self.assertEqual(item["oil_direction"]["structural_risk_label"], "상승")

    def test_gold_dollar_oil_ease_news_separates_fx_and_crude_direction(self):
        item = classify_news_item({
            "title": "Gold rises as dollar, oil ease on U.S.-Iran deal prospects",
            "summary": "Gold gained while the dollar and crude oil eased.",
            "source": "MarketWatch",
        })

        assets = {asset["asset"]: asset for asset in item["affected_assets"]}
        self.assertEqual(item["primary_theme"], "currency_fx")
        self.assertEqual(assets["GOLD"]["direction"], "positive")
        self.assertEqual(assets["USD"]["direction"], "negative")
        self.assertIn(assets["BRENT"]["direction"], {"negative", "neutral"})

    def test_market_schedule_news_is_excluded_from_core_news(self):
        schedule = calculate_news_impact(classify_news_item({
            "title": "Is the stock market open on Memorial Day?",
            "summary": "NYSE and Nasdaq are closed for the holiday.",
            "source": "MarketWatch",
        }))

        selected = select_report_news([schedule], max_items=4)

        self.assertEqual(schedule["primary_theme"], "market_schedule")
        self.assertEqual(schedule["report_priority"], "exclude")
        self.assertEqual(selected, [])

    def test_oil_change_story_is_not_crude_energy_news(self):
        item = classify_news_item({
            "title": "Buckle up for $120 oil changes as car care gets more expensive",
            "summary": "Drivers are delaying maintenance and may face bigger repairs.",
            "source": "MarketWatch",
        })

        self.assertEqual(item["primary_theme"], "autos")
        self.assertNotEqual(item["primary_theme"], "energy_inflation")
        self.assertNotIn("BRENT", [asset["asset"] for asset in item["affected_assets"]])

    def test_fed_news_is_rates_policy_even_with_ai_keyword(self):
        item = classify_news_item({
            "title": "Fed chair comments push yields higher and pressure Nasdaq AI winners",
            "summary": "Investors reassessed the rate outlook after the Fed remarks.",
            "source": "Reuters_Biz",
        })

        self.assertEqual(item["primary_theme"], "rates_policy")
        self.assertNotEqual(item["primary_theme"], "ai_infrastructure")
        self.assertIn("fed_news_not_ai", item["why_not_other_themes"])

    def test_consumer_company_specific_news_is_excluded(self):
        item = classify_news_item({
            "title": "Lululemon founder escalates governance dispute",
            "summary": "The dispute is company specific and has no semiconductor supply-chain readthrough.",
            "source": "Yahoo_Finance",
        })

        self.assertEqual(item["primary_theme"], "consumer")
        self.assertIn("company_specific", item["secondary_themes"])
        self.assertFalse(item["should_include_in_report"])
        self.assertEqual(item["report_priority"], "exclude")
        self.assertNotIn("semiconductor", [item["primary_theme"], *item["secondary_themes"]])

    def test_rating_word_does_not_trigger_rates_policy(self):
        item = classify_news_item({
            "title": "Evercore ISI reiterates CoreWeave stock rating on AI demand outlook",
            "summary": "Analyst rating note for one company.",
            "source": "news_archive",
        })

        self.assertNotEqual(item["primary_theme"], "rates_policy")
        self.assertNotIn("fed_news_not_ai", item["why_not_other_themes"])

    def test_company_bond_sale_is_not_rates_policy(self):
        item = classify_news_item({
            "title": "Ecolab launches $5B bond sale to fund CoolIT acquisition",
            "summary": "Company financing transaction.",
            "source": "news_archive",
        })

        self.assertEqual(item["primary_theme"], "company_specific")
        self.assertNotEqual(item["primary_theme"], "rates_policy")

    def test_impact_score_and_selector_filter_low_relevance_news(self):
        oil = calculate_news_impact(classify_news_item({
            "title": "Brent crude spikes on Middle East supply risk",
            "summary": "Oil prices rose and inflation expectations moved higher.",
            "source": "Reuters_Biz",
        }))
        consumer = calculate_news_impact(classify_news_item({
            "title": "Lululemon founder sends another letter to board",
            "summary": "Company governance dispute remains contained.",
            "source": "Yahoo_Finance",
        }))

        self.assertGreaterEqual(oil["impact_score"], 65)
        self.assertEqual(oil["importance_text"], oil["investment_implication"])
        selected = select_report_news([oil, consumer], max_items=4)
        self.assertEqual([item["primary_theme"] for item in selected], ["energy_inflation"])

    def test_historical_summary_changes_news_impact_context(self):
        event = classify_news_item({
            "title": "Brent crude spikes on Middle East supply risk",
            "summary": "Oil prices rose and inflation expectations moved higher.",
            "source": "Reuters_Biz",
        })
        base = calculate_news_impact(event)
        with_history = calculate_news_impact(event, historical_summary={
            "similar_event_count": 12,
            "median_abnormal_return_1d": -1.2,
            "median_abnormal_return_5d": -2.8,
            "directional_hit_rate": 0.68,
            "avg_volume_zscore": 1.4,
            "historical_impact_score": 82,
        })

        self.assertGreater(with_history["impact_score"], base["impact_score"])
        self.assertEqual(with_history["historical_reaction"]["similar_event_count"], 12)
        self.assertEqual(with_history["historical_impact_score"], 82)
        self.assertEqual(with_history["directional_confidence_score"], 68)
        self.assertIn(with_history["trading_signal_strength"], {"중간", "높음"})

    def test_historical_summary_with_low_sample_is_not_a_trading_signal(self):
        event = classify_news_item({
            "title": "Brent crude spikes on Middle East supply risk",
            "summary": "Oil prices rose and inflation expectations moved higher.",
            "source": "Reuters_Biz",
        })
        scored = calculate_news_impact(event, historical_summary={
            "similar_event_count": 1,
            "median_abnormal_return_1d": -1.2,
            "directional_hit_rate": 0.21,
            "historical_impact_score": 50,
        })

        self.assertFalse(scored["historical_reaction"]["sample_sufficient"])
        self.assertIsNone(scored["directional_confidence_score"])
        self.assertEqual(scored["trading_signal_strength"], "낮음")

    def test_news_event_store_and_similar_event_summary(self):
        event = calculate_news_impact(classify_news_item({
            "title": "Brent crude spikes on Middle East supply risk",
            "summary": "Oil prices rose and inflation expectations moved higher.",
            "source": "Reuters_Biz",
            "link": "https://example.com/brent",
        }))

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "news.db"
            ensure_news_event_tables(db_path)
            inserted = save_news_events([event], db_path=db_path)
            self.assertEqual(inserted, 1)

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO news_event_outcomes (
                        news_id, asset, abnormal_return_1d, abnormal_return_5d,
                        volume_zscore_1d, realized_direction, direction_hit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event["news_id"], "NASDAQ", -1.2, -2.8, 1.4, "negative", 1),
                )
                conn.commit()

            summary = find_similar_event_summary(
                primary_theme="energy_inflation",
                asset="NASDAQ",
                expected_direction="negative",
                db_path=db_path,
            )
            self.assertEqual(summary["similar_event_count"], 1)
            self.assertLess(summary["median_abnormal_return_1d"], 0)
            self.assertGreaterEqual(summary["historical_impact_score"], 50)

    def test_score_news_archive_backfills_snapshot_rows(self):
        original_snapshot_db = snapshot_collector.SNAPSHOT_DB
        with TemporaryDirectory() as tmpdir:
            snapshot_db = Path(tmpdir) / "snapshots.db"
            event_db = Path(tmpdir) / "events.db"
            snapshot_collector.SNAPSHOT_DB = snapshot_db
            snapshot_collector.init_snapshot_db()
            with sqlite3.connect(snapshot_db) as conn:
                conn.execute(
                    """
                    INSERT INTO news_archive (timestamp, headline, hash, theme, relevance_score)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "2026-05-20T09:50:00+09:00",
                        "Crude oil edges lower as traders await clarity on U.S.-Iran talks",
                        "oil-1",
                        "AI",
                        0.5,
                    ),
                )
                conn.commit()

            result = snapshot_collector.score_news_archive(db_path=snapshot_db, event_db_path=event_db)
            self.assertEqual(result["scored"], 1)

            with sqlite3.connect(snapshot_db) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM news_archive").fetchone()
                self.assertEqual(row["primary_theme"], "energy_inflation")
                self.assertGreaterEqual(row["impact_score"], 65)
                self.assertEqual(row["should_include_in_report"], 1)
                self.assertEqual(row["theme"], "energy_inflation")

            with sqlite3.connect(event_db) as conn:
                saved = conn.execute("SELECT COUNT(*) FROM news_events").fetchone()[0]
                self.assertEqual(saved, 1)
        snapshot_collector.SNAPSHOT_DB = original_snapshot_db


if __name__ == "__main__":
    unittest.main()
