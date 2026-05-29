import unittest

from src.position_tracker import parse_position_input


class PositionParserTest(unittest.TestCase):
    def test_underscore_restores_spaces_for_korean_listed_english_name(self):
        parsed = parse_position_input("LS_ELECTRIC 284000 1")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "LS ELECTRIC")
        self.assertEqual(parsed["ticker"], "010120")
        self.assertEqual(parsed["market"], "KR")
        self.assertEqual(parsed["currency"], "KRW")
        self.assertEqual(parsed["entry_price"], 284000)
        self.assertEqual(parsed["quantity"], 1)

    def test_plain_spaced_ls_electric_is_also_treated_as_korean_listing(self):
        parsed = parse_position_input("LS ELECTRIC 284000 1")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "LS ELECTRIC")
        self.assertEqual(parsed["ticker"], "010120")
        self.assertEqual(parsed["market"], "KR")
        self.assertEqual(parsed["currency"], "KRW")


if __name__ == "__main__":
    unittest.main()
