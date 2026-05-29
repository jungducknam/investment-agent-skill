"""
news_translator.py — display-only translation for foreign report news.

Translations are cached and must not be used as the source of investment
classification or scoring. The original title/summary remains the canonical
input for the news intelligence layer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import DB_PATH, DEEPL_API_KEY, DEEPL_API_URL, DEEPL_TARGET_LANG

logger = logging.getLogger(__name__)


def translate_report_news_items(
    news_items: list[dict[str, Any]],
    *,
    db_path: Path | str = DB_PATH,
    api_key: str | None = None,
    api_url: str = DEEPL_API_URL,
    target_lang: str = DEEPL_TARGET_LANG,
) -> list[dict[str, Any]]:
    """Translate only foreign-language news selected for the report."""
    if not news_items:
        return []
    key = api_key if api_key is not None else DEEPL_API_KEY
    if not key:
        return [dict(item) for item in news_items]

    ensure_translation_cache(db_path)
    translated = []
    for item in news_items:
        translated.append(
            translate_news_item(
                item,
                db_path=db_path,
                api_key=key,
                api_url=api_url,
                target_lang=target_lang,
            )
        )
    return translated


def translate_news_item(
    item: dict[str, Any],
    *,
    db_path: Path | str = DB_PATH,
    api_key: str,
    api_url: str = DEEPL_API_URL,
    target_lang: str = DEEPL_TARGET_LANG,
) -> dict[str, Any]:
    result = dict(item)
    title = str(result.get("title") or result.get("headline") or "").strip()
    summary = str(result.get("summary") or "").strip()
    if not should_translate_news(title, summary, result.get("source")):
        return result

    cache_key = _cache_key(title, summary, target_lang)
    cached = load_translation(cache_key, db_path=db_path)
    if cached:
        result.update(cached)
        return result

    texts = [title]
    if summary:
        texts.append(summary)
    try:
        translations = deepl_translate_texts(
            texts,
            api_key=api_key,
            api_url=api_url,
            target_lang=target_lang,
        )
    except Exception as exc:
        logger.warning("DeepL 뉴스 번역 실패: %s", exc)
        return result

    payload = {
        "translated_title": translations[0] if translations else "",
        "translated_summary": translations[1] if len(translations) > 1 else "",
        "translation_provider": "deepl",
        "translation_target_lang": target_lang,
    }
    save_translation(cache_key, payload, db_path=db_path)
    result.update(payload)
    return result


def deepl_translate_texts(
    texts: list[str],
    *,
    api_key: str,
    api_url: str = DEEPL_API_URL,
    target_lang: str = DEEPL_TARGET_LANG,
    timeout: int = 20,
) -> list[str]:
    clean_texts = [str(text).strip() for text in texts if str(text).strip()]
    if not clean_texts:
        return []

    form: list[tuple[str, str]] = [("target_lang", target_lang)]
    form.extend(("text", text) for text in clean_texts)
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        api_url,
        data=body,
        headers={
            "Authorization": f"DeepL-Auth-Key {api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [item.get("text", "") for item in payload.get("translations", [])]


def should_translate_news(title: str, summary: str = "", source: str | None = None) -> bool:
    text = f"{title} {summary}".strip()
    if not text:
        return False
    if _hangul_ratio(text) >= 0.08:
        return False
    if _latin_ratio(text) < 0.25:
        return False
    return True


def ensure_translation_cache(db_path: Path | str = DB_PATH) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_translation_cache (
                cache_key TEXT PRIMARY KEY,
                translated_title TEXT,
                translated_summary TEXT,
                translation_provider TEXT,
                translation_target_lang TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def load_translation(cache_key: str, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    ensure_translation_cache(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT translated_title, translated_summary, translation_provider, translation_target_lang
            FROM news_translation_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
    return dict(row) if row else None


def save_translation(cache_key: str, payload: dict[str, Any], db_path: Path | str = DB_PATH) -> None:
    ensure_translation_cache(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO news_translation_cache (
                cache_key, translated_title, translated_summary,
                translation_provider, translation_target_lang
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                translated_title = excluded.translated_title,
                translated_summary = excluded.translated_summary,
                translation_provider = excluded.translation_provider,
                translation_target_lang = excluded.translation_target_lang
            """,
            (
                cache_key,
                payload.get("translated_title") or "",
                payload.get("translated_summary") or "",
                payload.get("translation_provider") or "deepl",
                payload.get("translation_target_lang") or DEEPL_TARGET_LANG,
            ),
        )
        conn.commit()


def _cache_key(title: str, summary: str, target_lang: str) -> str:
    raw = f"{target_lang}\n{title}\n{summary}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hangul_ratio(text: str) -> float:
    chars = [char for char in str(text) if char.isalpha()]
    if not chars:
        return 0.0
    hangul = sum(1 for char in chars if "\uac00" <= char <= "\ud7a3")
    return hangul / len(chars)


def _latin_ratio(text: str) -> float:
    chars = [char for char in str(text) if char.isalpha()]
    if not chars:
        return 0.0
    latin = sum(1 for char in chars if re.match(r"[A-Za-z]", char))
    return latin / len(chars)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()
