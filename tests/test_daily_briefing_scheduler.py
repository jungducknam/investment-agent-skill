import unittest
from datetime import datetime

from src.config import KST
from src.bot import is_daily_briefing_day, next_daily_briefing_time


class DailyBriefingSchedulerTest(unittest.TestCase):
    def test_next_briefing_time_skips_sunday(self):
        saturday_after_run = KST.localize(datetime(2026, 5, 16, 8, 0))

        next_run = next_daily_briefing_time(saturday_after_run)

        self.assertEqual(next_run, KST.localize(datetime(2026, 5, 18, 7, 0)))

    def test_sunday_is_not_a_briefing_day(self):
        sunday = KST.localize(datetime(2026, 5, 17, 7, 0))

        self.assertFalse(is_daily_briefing_day(sunday))

    def test_before_seven_runs_today_on_weekday(self):
        monday_before_run = KST.localize(datetime(2026, 5, 18, 6, 30))

        next_run = next_daily_briefing_time(monday_before_run)

        self.assertEqual(next_run, KST.localize(datetime(2026, 5, 18, 7, 0)))


if __name__ == "__main__":
    unittest.main()
