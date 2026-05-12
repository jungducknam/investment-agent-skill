"""Structured activity logging for Telegram update handling."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR, KST

DEFAULT_ACTIVITY_LOG = DATA_DIR / "activity.log"

_REDACTIONS = [
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"), "[redacted-telegram-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"), "[redacted-api-key]"),
]


def redact_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    result = value
    for pattern, replacement in _REDACTIONS:
        result = pattern.sub(replacement, result)
    return result


def append_activity(
    event: str,
    *,
    path: Path = DEFAULT_ACTIVITY_LOG,
    now: datetime | None = None,
    **fields: Any,
) -> None:
    timestamp = now or datetime.now(KST)
    record = {
        "ts_kst": timestamp.isoformat(),
        "event": event,
    }
    record.update({key: redact_value(value) for key, value in fields.items()})

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        fh.write("\n")
