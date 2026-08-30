import os
import sqlite3
import threading
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DOMAIN_STORE_DB_PATH", os.path.join(os.path.dirname(__file__), "domains.db"))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS domain_config (
                domain TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.commit()


def save_domains(domains: List[str], disabled: List[str]) -> None:
    disabled_set = {(d or "").strip().lower() for d in disabled if (d or "").strip()}
    with _lock, _conn() as conn:
        for domain in domains:
            normalized = (domain or "").strip().lower()
            if not normalized:
                continue
            enabled = 0 if normalized in disabled_set else 1
            conn.execute(
                "INSERT INTO domain_config (domain, enabled) VALUES (?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET enabled = excluded.enabled",
                (normalized, enabled),
            )
        conn.commit()


def get_domains() -> List[Dict[str, object]]:
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT domain, enabled FROM domain_config ORDER BY domain ASC").fetchall()
    return [{"domain": r["domain"], "enabled": bool(r["enabled"])} for r in rows]


def get_enabled_domains() -> List[str]:
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT domain FROM domain_config WHERE enabled = 1 ORDER BY domain ASC").fetchall()
    return [r["domain"] for r in rows]


init_db()
