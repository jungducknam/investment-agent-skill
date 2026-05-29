import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from src.activity_log import append_activity, redact_value
from src.config import KST


class ActivityLogTest(unittest.TestCase):
    def test_redact_value_masks_common_api_tokens(self):
        text = (
            "bot=1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi "
            "key=sk-abcdefghijklmnopqrstuvwxyz1234567890"
        )

        redacted = redact_value(text)

        self.assertNotIn("1234567890:", redacted)
        self.assertNotIn("sk-", redacted)
        self.assertIn("[redacted-telegram-token]", redacted)
        self.assertIn("[redacted-api-key]", redacted)

    def test_append_activity_writes_json_line_with_kst_timestamp(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "activity.log"

            append_activity(
                "message_received",
                path=path,
                now=KST.localize(datetime(2026, 5, 12, 14, 17)),
                chat_id=896018134,
                text="오늘 리포트",
            )

            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["event"], "message_received")
            self.assertEqual(record["chat_id"], 896018134)
            self.assertEqual(record["text"], "오늘 리포트")
            self.assertTrue(record["ts_kst"].startswith("2026-05-12T14:17:00"))


if __name__ == "__main__":
    unittest.main()
