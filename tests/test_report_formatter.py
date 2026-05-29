import unittest

from src.report_formatter import build_msg1, build_msg2


class ReportFormatterTest(unittest.TestCase):
    def test_msg1_always_includes_bot_visible_news_summary(self):
        report = {
            "report_date": "2026년 05월 16일",
            "market_summary": {
                "overall_sentiment": "약세",
                "sentiment_score": 3,
                "key_theme": "국장 변동성 확대",
                "sector_rotation": "방산과 전력인프라 중심으로 선별 접근.",
                "data_quality_notes": "국장 가격은 KIS 기준, 미국 가격은 마지막 정규장 종가 기준입니다.",
                "risk_factors": ["KOSPI 급락", "환율 변동성"],
            },
            "portfolio_strategy": {
                "cash_reserve_pct": 30,
                "long_term_allocation": "우량주 분할 매수",
                "swing_strategy": "과열 종목 추격 금지",
                "daytrading_focus": "변동성 제한",
                "overall_advice": "현금 비중을 유지합니다.",
            },
            "_indices": {
                "KOSPI": {"price": 7493.18, "change_pct": -6.12},
                "KOSDAQ": {"price": 1129.82, "change_pct": -5.14},
            },
            "_events": ["미국 CPI"],
            "_news_headlines": ["반도체 수출 증가에도 국내 증시 변동성이 확대됐다."],
            "_theme_news": {"방산": ["방산 수주 모멘텀이 이어졌다."]},
            "_detailed_news": [
                {
                    "title": "반도체 수출 증가에도 국내 증시 변동성이 확대됐다.",
                    "summary": "AI와 HBM 수요는 견조하지만 환율과 금리 부담이 이어졌다.",
                    "link": "https://example.com/news/semiconductor",
                    "source": "Reuters_Biz",
                    "published": "05/18 06:10",
                    "themes": ["반도체", "AI"],
                    "why": "반도체 이슈입니다. 한국/미국 기술주와 HBM·장비주 수급에 직접 연결됩니다.",
                    "impact_score": 73,
                    "directional_confidence_score": 21,
                    "trading_signal_strength": "낮음",
                    "historical_reaction": {"similar_event_count": 1, "sample_sufficient": False},
                }
            ],
            "_historical_news_context": "━━━ 이전 중요 뉴스 ━━━\n• [20260515][AI] AI 투자 확대 뉴스",
        }

        message = build_msg1(report)

        self.assertIn("*📰 핵심 뉴스 요약*", message)
        self.assertIn("출처: Reuters_Biz", message)
        self.assertIn("요약:", message)
        self.assertIn("영향경로:", message)
        self.assertIn("영향도: 73/100", message)
        self.assertIn("방향 확신도: 21/100", message)
        self.assertIn("거래 신호 강도: 낮음", message)
        self.assertIn("과거반응: 표본 부족, 참고 불가", message)
        self.assertIn("원문: [열기](https://example.com/news/semiconductor)", message)
        self.assertIn("*🧪 데이터 품질*", message)
        self.assertIn("국장 가격은 KIS 기준", message)

    def test_msg1_normalizes_sentiment_score_display(self):
        report = {
            "report_date": "2026년 05월 20일",
            "market_summary": {
                "overall_sentiment": "약세",
                "sentiment_score": 18.4,
                "key_theme": "위험자산 약세",
                "sector_rotation": "방어적 접근.",
                "risk_factors": ["금리 부담"],
            },
            "portfolio_strategy": {
                "cash_reserve_pct": 70,
                "long_term_allocation": "대기",
                "swing_strategy": "대기",
                "daytrading_focus": "제한",
                "overall_advice": "신규 매수 금지",
            },
            "_indices": {},
            "_events": [],
            "_detailed_news": [],
        }

        message = build_msg1(report)

        self.assertIn("시장 심리 1.8/10", message)
        self.assertNotIn("18.4/10", message)

    def test_msg1_marks_low_neutral_sentiment_as_watchful(self):
        report = _base_msg1_report()
        report["market_summary"]["overall_sentiment"] = "중립"
        report["market_summary"]["sentiment_score"] = 4.3

        message = build_msg1(report)

        self.assertIn("시장 심리 4.3/10 — 중립 하단 / 관망", message)

    def test_msg1_displays_event_sections_by_category(self):
        report = _base_msg1_report()
        report["_event_sections"] = {
            "this_week": ["05/28 미국 PCE 물가"],
            "next_major": ["06/10 미국 CPI", "06/16~17 FOMC"],
            "market_schedule": ["미국장 Memorial Day 휴장 후 재개장"],
        }

        message = build_msg1(report)

        self.assertIn("이번 주:", message)
        self.assertIn("• 05/28 미국 PCE 물가", message)
        self.assertIn("다음 주요 이벤트:", message)
        self.assertIn("• 06/10 미국 CPI", message)
        self.assertIn("시장 일정 주의:", message)
        self.assertIn("• 미국장 Memorial Day 휴장 후 재개장", message)

    def test_recommendations_are_mobile_readable_without_wide_code_table(self):
        report = {
            "recommendations": [
                {
                    "rank": 1,
                    "name": "HD현대일렉트릭",
                    "ticker": "267260.KS",
                    "market": "KR",
                    "style": "스윙",
                    "current_price": 382000,
                    "target_price_1": 405000,
                    "target_price_2": 430000,
                    "stop_loss": 360000,
                    "position_size_pct": 5,
                    "holding_period": "1~4주",
                    "currency": "KRW",
                    "upside_pct": 12.6,
                    "confidence_score": 72,
                    "action": "conditional_buy",
                    "is_executable": True,
                    "risk_gate_status": "PASS",
                    "risk_reward_1": 2.1,
                    "invalidation_condition": "KOSPI가 전일 저점을 재이탈하면 대기.",
                    "investment_rationale": ["전력 인프라 수주 모멘텀이 유지되고 있다."],
                },
                {
                    "rank": 2,
                    "name": "NVIDIA",
                    "ticker": "NVDA",
                    "market": "US",
                    "style": "장기",
                    "current_price": 920.5,
                    "target_price_1": 980,
                    "target_price_2": 1050,
                    "stop_loss": 870,
                    "position_size_pct": 4,
                    "holding_period": "3개월 이상",
                    "currency": "USD",
                    "upside_pct": 14.1,
                    "investment_rationale": ["AI 가속기 수요가 견조하다."],
                },
            ],
            "waiting_list": [
                {"name": "GE Vernova", "ticker": "GEV", "reason": "종가 확인 필요", "condition": "다음날 저점 방어"},
            ],
            "rejected_candidates": [
                {"name": "AMD", "ticker": "AMD", "reason": "poor_rr", "details": "poor_rr"},
            ],
            "watchlist": ["삼성전자", "AMD"],
        }

        message = build_msg2(report)

        self.assertIn("*오늘의 액션 플랜*", message)
        self.assertNotIn("TOP 10", message)
        self.assertNotIn("```", message)
        self.assertIn("진입:", message)
        self.assertIn("목표:", message)
        self.assertIn("손절:", message)
        self.assertIn("비중:", message)
        self.assertIn("*조건부 진입*", message)
        self.assertIn("*관심/대기*", message)
        self.assertIn("*실행금지*", message)
        self.assertIn("손익비: 2.1R", message)
        self.assertLessEqual(max(len(line) for line in message.splitlines()), 90)

    def test_recommendations_tolerate_missing_ai_price_fields(self):
        report = {
            "recommendations": [
                {
                    "rank": 1,
                    "name": "가격누락종목",
                    "ticker": "MISS",
                    "market": "US",
                    "style": "스윙",
                    "current_price": None,
                    "target_price_1": None,
                    "target_price_2": None,
                    "stop_loss": None,
                    "position_size_pct": 3,
                    "holding_period": "1~4주",
                    "currency": "USD",
                    "upside_pct": None,
                    "investment_rationale": [],
                },
            ],
            "watchlist": [],
        }

        message = build_msg2(report)

        self.assertIn("*관심/대기*", message)
        self.assertIn("가격누락종목", message)

    def test_watchlist_ticker_strings_show_stock_names(self):
        report = {
            "recommendations": [],
            "watchlist": ["NVDA", "005930.KS", "042700"],
        }

        message = build_msg2(report)

        self.assertIn("NVIDIA `NVDA`", message)
        self.assertIn("삼성전자 `005930.KS`", message)
        self.assertIn("한미반도체 `042700`", message)

    def test_rejected_candidates_are_visible_in_msg2(self):
        report = {
            "recommendations": [],
            "rejected_candidates": [
                {
                    "ticker": "NVDA",
                    "reason": "overbought",
                    "details": "overbought; above_entry",
                }
            ],
            "watchlist": [],
        }

        message = build_msg2(report)

        self.assertIn("*실행금지*", message)
        self.assertIn("`NVDA`: 리스크 게이트 미통과: 과열 · 진입가 초과", message)

    def test_rejected_candidates_tolerate_ticker_strings(self):
        report = {
            "recommendations": [],
            "rejected_candidates": ["NVDA", "005930.KS"],
            "watchlist": [],
        }

        message = build_msg2(report)

        self.assertIn("*실행금지*", message)
        self.assertIn("NVIDIA `NVDA`", message)
        self.assertIn("삼성전자 `005930.KS`", message)

    def test_watchlist_duplicates_are_removed_by_ticker_or_name(self):
        report = {
            "recommendations": [],
            "waiting_list": [{"name": "SK하이닉스", "ticker": "000660.KS", "reason": "파업 후속 확인"}],
            "rejected_candidates": [],
            "watchlist": ["SK하이닉스", "000660.KS", "HD현대일렉트릭", "267260.KS", "GEV", "GEV"],
        }

        message = build_msg2(report)

        self.assertEqual(message.count("SK하이닉스"), 1)
        self.assertEqual(message.count("HD현대일렉트릭"), 1)
        self.assertEqual(message.count("GEV"), 1)

    def test_rejected_candidate_shows_specific_gate_reasons(self):
        report = {
            "recommendations": [],
            "rejected_candidates": [
                {
                    "ticker": "GEV",
                    "reason": "risk_gate_failed",
                    "failed_rules": ["overbought", "poor_rr", "data_quality_fail", "event_risk_48h", "crisis_no_new_buy"],
                    "details": "overbought; poor_rr; data_quality_fail; event_risk_48h; crisis_no_new_buy",
                }
            ],
            "watchlist": [],
        }

        message = build_msg2(report)

        self.assertIn("과열", message)
        self.assertIn("손익비 부족", message)
        self.assertIn("데이터 품질 낮음", message)
        self.assertIn("이벤트 리스크", message)
        self.assertIn("레짐 불일치", message)

    def test_positions_are_shown_before_action_plan(self):
        report = {
            "_positions": [
                {
                    "name": "HD한국조선해양",
                    "ticker": "009540",
                    "market": "KR",
                    "currency": "KRW",
                    "quantity": 3,
                    "entry_price": 411000,
                    "current_price": 421000,
                    "defense_line": 411000,
                    "pnl_pct": 2.43,
                    "action": "유지",
                    "add_buy_policy": "보류",
                }
            ],
            "recommendations": [],
            "watchlist": [],
        }

        message = build_msg2(report)

        self.assertLess(message.index("📌 *보유종목 관리*"), message.index("🎯 *오늘의 액션 플랜*"))
        self.assertIn("*HD한국조선해양* `009540`", message)
        self.assertIn("추가매수 보류", message)

    def test_waiting_copy_removes_buy_entry_access_language(self):
        report = {
            "recommendations": [],
            "waiting_list": [
                {
                    "name": "삼성전자",
                    "ticker": "005930.KS",
                    "reason": "조정 시 매수 관점 접근",
                    "condition": "지지 확인 시 진입 고려",
                }
            ],
            "watchlist": [],
        }

        message = build_msg2(report)
        waiting_block = message.split("🔵 *관심/대기*", 1)[1].split("\n\n", 1)[0]

        self.assertNotIn("매수", waiting_block)
        self.assertNotIn("진입", waiting_block)
        self.assertNotIn("접근", waiting_block)
        self.assertIn("관찰", waiting_block)
        self.assertIn("재검토", waiting_block)


def _base_msg1_report() -> dict:
    return {
        "report_date": "2026년 05월 26일",
        "market_summary": {
            "overall_sentiment": "중립",
            "sentiment_score": 4.3,
            "key_theme": "관망",
            "sector_rotation": "선별 관찰.",
            "risk_factors": ["금리 부담"],
        },
        "portfolio_strategy": {
            "cash_reserve_pct": 40,
            "long_term_allocation": "대기",
            "swing_strategy": "대기",
            "daytrading_focus": "제한",
            "overall_advice": "신규 매수는 제한합니다.",
        },
        "_indices": {},
        "_detailed_news": [],
    }


if __name__ == "__main__":
    unittest.main()
