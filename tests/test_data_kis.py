import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import data_kis


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class DataKisTest(unittest.TestCase):
    def setUp(self):
        data_kis._TOKEN_CACHE["access_token"] = None
        data_kis._TOKEN_CACHE["expires_at"] = 0.0
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.token_cache_file = Path(self.tmpdir.name) / "kis_token.json"
        self.token_cache_patch = patch.object(data_kis, "TOKEN_CACHE_FILE", self.token_cache_file)
        self.token_cache_patch.start()
        self.addCleanup(self.token_cache_patch.stop)
        self.rate_limit_patch = patch.object(data_kis, "KIS_REQUEST_INTERVAL_SEC", 0.0)
        self.rate_limit_patch.start()
        self.addCleanup(self.rate_limit_patch.stop)

    def test_domestic_quote_normalizes_kis_response(self):
        responses = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({
                "rt_cd": "0",
                "output": {
                    "stck_prpr": "72600",
                    "prdy_ctrt": "1.40",
                    "prdy_vrss": "1000",
                    "stck_oprc": "71600",
                    "stck_hgpr": "72800",
                    "stck_lwpr": "71000",
                    "acml_vol": "12345678",
                    "acml_tr_pbmn": "890000000000",
                },
            }),
        ]

        with patch.object(data_kis, "KIS_APP_KEY", "app-key"), \
             patch.object(data_kis, "KIS_APP_SECRET", "app-secret"), \
             patch.object(data_kis.request, "urlopen", side_effect=responses):
            quote = data_kis.get_domestic_stock_quote("5930")

        self.assertEqual(quote["price"], 72600.0)
        self.assertEqual(quote["change_pct"], 1.4)
        self.assertEqual(quote["volume"], 12345678)
        self.assertEqual(quote["source"], "KIS")

    def test_quote_returns_none_without_credentials(self):
        with patch.object(data_kis, "KIS_APP_KEY", ""), \
             patch.object(data_kis, "KIS_APP_SECRET", ""):
            self.assertIsNone(data_kis.get_domestic_stock_quote("005930"))

    def test_access_token_uses_disk_cache_before_new_issue(self):
        self.token_cache_file.write_text(json.dumps({
            "access_token": "cached-token",
            "expires_at": time.time() + 3600,
        }))

        with patch.object(data_kis, "KIS_APP_KEY", "app-key"), \
             patch.object(data_kis, "KIS_APP_SECRET", "app-secret"), \
             patch.object(data_kis, "_request_json") as request_json:
            token = data_kis.get_access_token()

        self.assertEqual(token, "cached-token")
        request_json.assert_not_called()

    def test_domestic_index_quote_normalizes_kis_response(self):
        responses = [
            FakeResponse({"access_token": "token", "expires_in": 86400}),
            FakeResponse({
                "rt_cd": "0",
                "output1": {
                    "bstp_nmix_prpr": "7493.18",
                    "bstp_nmix_prdy_ctrt": "-6.12",
                    "bstp_nmix_prdy_vrss": "-488.23",
                    "bstp_nmix_oprc": "7981.41",
                    "bstp_nmix_hgpr": "7981.41",
                    "bstp_nmix_lwpr": "7493.18",
                    "acml_vol": "885627",
                    "acml_tr_pbmn": "123456789",
                },
                "output2": [{"stck_bsop_date": "20260515"}],
            }),
        ]

        with patch.object(data_kis, "KIS_APP_KEY", "app-key"), \
             patch.object(data_kis, "KIS_APP_SECRET", "app-secret"), \
             patch.object(data_kis.request, "urlopen", side_effect=responses):
            quote = data_kis.get_domestic_index_quote("KOSPI")

        self.assertEqual(quote["price"], 7493.18)
        self.assertEqual(quote["change_pct"], -6.12)
        self.assertEqual(quote["business_date"], "20260515")
        self.assertEqual(quote["source"], "KIS")


if __name__ == "__main__":
    unittest.main()
