import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("CACHE_DB_PATH", os.path.join(os.path.dirname(__file__), "cache.db"))
DEFAULT_TTL = int(os.getenv("CACHE_TTL_SECONDS", "900"))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feed_cache (
                cache_key TEXT PRIMARY KEY,
                response_json TEXT NOT NULL DEFAULT '',
                cached_at REAL NOT NULL DEFAULT 0.0,
                ttl_seconds INTEGER NOT NULL DEFAULT 900
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feed_cache_cached ON feed_cache(cached_at)")
        conn.commit()


def _make_key(feed_url: str) -> str:
    return hashlib.sha256(feed_url.encode()).hexdigest()


def get(feed_url: str) -> Optional[str]:
    key = _make_key(feed_url)
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT response_json, cached_at, ttl_seconds FROM feed_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    if time.time() - row["cached_at"] > row["ttl_seconds"]:
        return None
    return row["response_json"]


def set(feed_url: str, response_text: str, ttl_seconds: int = DEFAULT_TTL) -> None:
    key = _make_key(feed_url)
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO feed_cache (cache_key, response_json, cached_at, ttl_seconds) VALUES (?, ?, ?, ?)",
            (key, response_text, time.time(), ttl_seconds),
        )
        conn.commit()


def clear_expired() -> int:
    now = time.time()
    with _lock, _conn() as conn:
        cursor = conn.execute(
            "DELETE FROM feed_cache WHERE cached_at + ttl_seconds < ?",
            (now,),
        )
        conn.commit()
        return cursor.rowcount


init_db()
