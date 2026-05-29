"""
data_kis.py - Korea Investment Open API quote client.

Only market-data endpoints are used here. API credentials must be supplied
through environment variables; never commit them into the repository.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from urllib import error as urlerror
from urllib import parse, request

from .config import DATA_DIR, KIS_APP_KEY, KIS_APP_SECRET, KIS_BASE_URL, KST

logger = logging.getLogger(__name__)

TOKEN_PATH = "/oauth2/tokenP"
DOMESTIC_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
DOMESTIC_QUOTE_TR_ID = "FHKST01010100"
DOMESTIC_INDEX_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
DOMESTIC_INDEX_TR_ID = "FHKUP03500100"

DOMESTIC_INDEX_CODES = {
    "KOSPI": "0001",
    "KOSDAQ": "1001",
}
KIS_REQUEST_INTERVAL_SEC = float(os.getenv("KIS_REQUEST_INTERVAL_SEC", "1.1"))
KIS_RATE_LIMIT_RETRY_SEC = float(os.getenv("KIS_RATE_LIMIT_RETRY_SEC", "2.0"))
KIS_RATE_LIMIT_MAX_RETRIES = int(os.getenv("KIS_RATE_LIMIT_MAX_RETRIES", "2"))

_TOKEN_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_TOKEN_CACHE = {
    "access_token": None,
    "expires_at": 0.0,
}
TOKEN_CACHE_FILE = DATA_DIR / "kis_token.json"


def is_kis_configured() -> bool:
    return bool(KIS_APP_KEY and KIS_APP_SECRET)


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _is_rate_limit_error(message: str) -> bool:
    return "EGW00201" in message or "거래건수를 초과" in message or "한도 초과" in message


def _request_json(method: str, path: str, *, headers=None, params=None, body=None, timeout=8) -> dict:
    global _LAST_REQUEST_AT

    url = KIS_BASE_URL.rstrip("/") + path
    if params:
        url += "?" + parse.urlencode(params)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req_headers = {
        "content-type": "application/json; charset=utf-8",
    }
    if headers:
        req_headers.update(headers)

    req = request.Request(url, data=data, headers=req_headers, method=method)
    for attempt in range(KIS_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            with _REQUEST_LOCK:
                if KIS_REQUEST_INTERVAL_SEC > 0:
                    elapsed = time.monotonic() - _LAST_REQUEST_AT
                    wait = KIS_REQUEST_INTERVAL_SEC - elapsed
                    if wait > 0:
                        time.sleep(wait)
                _LAST_REQUEST_AT = time.monotonic()
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urlerror.HTTPError as exc:
            safe_body = exc.read().decode("utf-8", errors="replace")[:300]
            message = f"KIS HTTP {exc.code}: {safe_body}"
            if attempt < KIS_RATE_LIMIT_MAX_RETRIES and _is_rate_limit_error(message):
                time.sleep(KIS_RATE_LIMIT_RETRY_SEC * (attempt + 1))
                continue
            raise RuntimeError(message) from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"KIS connection failed: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("KIS returned invalid JSON") from exc


def _token_expiry(payload: dict) -> float:
    expires_in = _to_int(payload.get("expires_in"))
    if expires_in:
        return time.time() + max(expires_in - 300, 60)

    expiry_text = payload.get("access_token_token_expired")
    if expiry_text:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(expiry_text, fmt).timestamp() - 300
            except ValueError:
                continue

    return time.time() + 60 * 60 * 23


def _load_disk_token() -> tuple[str, float] | tuple[None, float]:
    try:
        payload = json.loads(TOKEN_CACHE_FILE.read_text())
        token = payload.get("access_token")
        expires_at = float(payload.get("expires_at") or 0)
        if token and time.time() < expires_at - 60:
            return token, expires_at
    except FileNotFoundError:
        return None, 0.0
    except Exception as exc:
        logger.debug("KIS token cache read failed: %s", exc)
    return None, 0.0


def _save_disk_token(token: str, expires_at: float) -> None:
    try:
        TOKEN_CACHE_FILE.parent.mkdir(exist_ok=True)
        tmp = TOKEN_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"access_token": token, "expires_at": expires_at}))
        os.chmod(tmp, 0o600)
        tmp.replace(TOKEN_CACHE_FILE)
        os.chmod(TOKEN_CACHE_FILE, 0o600)
    except Exception as exc:
        logger.debug("KIS token cache write failed: %s", exc)


def get_access_token(force_refresh: bool = False) -> str | None:
    if not is_kis_configured():
        return None

    now = time.time()
    cached = _TOKEN_CACHE.get("access_token")
    if cached and not force_refresh and now < float(_TOKEN_CACHE.get("expires_at", 0)):
        return cached

    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get("access_token")
        if cached and not force_refresh and time.time() < float(_TOKEN_CACHE.get("expires_at", 0)):
            return cached

        if not force_refresh:
            disk_token, disk_expires_at = _load_disk_token()
            if disk_token:
                _TOKEN_CACHE["access_token"] = disk_token
                _TOKEN_CACHE["expires_at"] = disk_expires_at
                return disk_token

        try:
            payload = _request_json(
                "POST",
                TOKEN_PATH,
                body={
                    "grant_type": "client_credentials",
                    "appkey": KIS_APP_KEY,
                    "appsecret": KIS_APP_SECRET,
                },
            )
        except RuntimeError as exc:
            logger.warning("KIS access token request failed: %s", exc)
            disk_token, disk_expires_at = _load_disk_token()
            if disk_token:
                _TOKEN_CACHE["access_token"] = disk_token
                _TOKEN_CACHE["expires_at"] = disk_expires_at
                return disk_token
            return None

        token = payload.get("access_token")
        if not token:
            logger.warning("KIS access token request failed: %s", payload.get("msg1") or payload.get("error_description") or "unknown error")
            return None

        _TOKEN_CACHE["access_token"] = token
        _TOKEN_CACHE["expires_at"] = _token_expiry(payload)
        _save_disk_token(token, float(_TOKEN_CACHE["expires_at"]))
        return token


def get_domestic_stock_quote(stock_code: str) -> dict | None:
    """Return a normalized quote for a KRX-listed stock code."""
    code = str(stock_code).zfill(6)
    token = get_access_token()
    if not token:
        return None

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": DOMESTIC_QUOTE_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }

    try:
        payload = _request_json("GET", DOMESTIC_QUOTE_PATH, headers=headers, params=params)
    except RuntimeError as exc:
        logger.warning("KIS quote request failed (%s): %s", code, exc)
        return None

    if payload.get("rt_cd") != "0":
        logger.warning("KIS quote returned error (%s): %s", code, payload.get("msg1") or "unknown error")
        return None

    output = payload.get("output") or {}
    price = _to_float(output.get("stck_prpr"))
    if price is None:
        return None

    return {
        "price": round(price, 2),
        "change_pct": _to_float(output.get("prdy_ctrt")),
        "change_price": _to_float(output.get("prdy_vrss")),
        "open": _to_float(output.get("stck_oprc")),
        "high": _to_float(output.get("stck_hgpr")),
        "low": _to_float(output.get("stck_lwpr")),
        "volume": _to_int(output.get("acml_vol")),
        "trading_value": _to_int(output.get("acml_tr_pbmn")),
        "source": "KIS",
    }


def get_domestic_stock_price(stock_code: str) -> float | None:
    quote = get_domestic_stock_quote(stock_code)
    return quote.get("price") if quote else None


def _latest_index_row(output2) -> dict:
    rows = output2 or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return {}

    valid_rows = [row for row in rows if isinstance(row, dict)]
    if not valid_rows:
        return {}
    return max(valid_rows, key=lambda row: str(row.get("stck_bsop_date") or ""))


def get_domestic_index_quote(index_name: str) -> dict | None:
    """Return a normalized KOSPI/KOSDAQ quote from KIS industry-index API."""
    code = DOMESTIC_INDEX_CODES.get(str(index_name).upper(), str(index_name))
    token = get_access_token()
    if not token:
        return None

    today = datetime.now(KST).date()
    start = today - timedelta(days=10)
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": DOMESTIC_INDEX_TR_ID,
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": today.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
    }

    try:
        payload = _request_json("GET", DOMESTIC_INDEX_PATH, headers=headers, params=params)
    except RuntimeError as exc:
        logger.warning("KIS index request failed (%s): %s", index_name, exc)
        return None

    if payload.get("rt_cd") != "0":
        logger.warning("KIS index returned error (%s): %s", index_name, payload.get("msg1") or "unknown error")
        return None

    output1 = payload.get("output1") or {}
    output2 = _latest_index_row(payload.get("output2"))
    data = output1 if _to_float(output1.get("bstp_nmix_prpr")) is not None else output2

    price = _to_float(data.get("bstp_nmix_prpr"))
    if price is None:
        return None

    return {
        "price": round(price, 2),
        "change_pct": _to_float(data.get("bstp_nmix_prdy_ctrt")),
        "change_price": _to_float(data.get("bstp_nmix_prdy_vrss")),
        "open": _to_float(data.get("bstp_nmix_oprc")),
        "high": _to_float(data.get("bstp_nmix_hgpr")),
        "low": _to_float(data.get("bstp_nmix_lwpr")),
        "volume": _to_int(data.get("acml_vol")),
        "trading_value": _to_int(data.get("acml_tr_pbmn")),
        "business_date": data.get("stck_bsop_date") or output2.get("stck_bsop_date"),
        "source": "KIS",
    }
