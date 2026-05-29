import unittest

from src.bot import menu_text_route


class MenuRoutingTest(unittest.TestCase):
    def test_text_menu_labels_route_to_button_actions(self):
        cases = {
            "📊 오늘 리포트": "report",
            "📋 포지션 현황": "positions",
            "➕ 포지션 등록": "add_position",
            "✨ 포지션 등록": "add_position",
            "⚡ 즉시 체크": "quick_check",
        }

        for text, route in cases.items():
            with self.subTest(text=text):
                self.assertEqual(menu_text_route(text), route)


if __name__ == "__main__":
    unittest.main()
