import json
import unittest
from unittest.mock import patch


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class GeminiClientTest(unittest.TestCase):
    def test_generate_gemini_report_text_posts_to_generate_content(self):
        from src import ai_client

        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeHTTPResponse({
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"recommendations": []}'},
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": "gemini-3.5-flash",
            })

        with patch.object(ai_client, "GEMINI_API_KEY", "test-key", create=True), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            text = ai_client.generate_gemini_report_text(
                "system prompt",
                "user prompt",
                max_output_tokens=123,
            )

        self.assertEqual(text, '{"recommendations": []}')
        self.assertIn("/models/gemini-3.5-flash:generateContent", captured["url"])
        self.assertEqual(captured["timeout"], 120)
        self.assertTrue(any(k.lower() == "x-goog-api-key" for k in captured["headers"]))
        self.assertEqual(captured["body"]["systemInstruction"]["parts"][0]["text"], "system prompt")
        self.assertEqual(captured["body"]["contents"][0]["parts"][0]["text"], "user prompt")
        self.assertEqual(captured["body"]["generationConfig"]["maxOutputTokens"], 123)
        self.assertEqual(
            captured["body"]["generationConfig"]["thinkingConfig"]["thinkingLevel"],
            "low",
        )


if __name__ == "__main__":
    unittest.main()
