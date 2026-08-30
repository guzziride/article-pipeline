import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("DRAFT_STORE_PATH", os.path.join(os.path.dirname(__file__), "drafts.db"))

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
            CREATE TABLE IF NOT EXISTS published_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                article_id TEXT NOT NULL DEFAULT '',
                draft TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_published_drafts_thread ON published_drafts(thread_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                draft TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_draft_versions_thread ON draft_versions(thread_id)"
        )
        conn.commit()


def get_drafts(thread_id: str) -> List[Dict[str, Any]]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT article_id, draft, published_at FROM published_drafts WHERE thread_id = ? ORDER BY id ASC",
            (thread_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_draft(thread_id: str, article_id: str, draft: str, published_at: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO published_drafts (thread_id, article_id, draft, published_at) VALUES (?, ?, ?, ?)",
            (thread_id, article_id, draft, published_at),
        )
        conn.commit()


def delete_draft(thread_id: str, index: int) -> None:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM published_drafts WHERE thread_id = ? ORDER BY id ASC",
            (thread_id,),
        ).fetchall()
        if 0 <= index < len(rows):
            row_id = rows[index]["id"]
            conn.execute("DELETE FROM published_drafts WHERE id = ?", (row_id,))
            conn.commit()


def delete_all_for_thread(thread_id: str) -> None:
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM published_drafts WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM draft_versions WHERE thread_id = ?", (thread_id,))
        conn.commit()


def add_version(thread_id: str, draft: str, source: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO draft_versions (thread_id, draft, source) VALUES (?, ?, ?)",
            (thread_id, draft, source),
        )
        conn.commit()


def get_versions(thread_id: str) -> List[Dict[str, Any]]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT draft, source, created_at FROM draft_versions WHERE thread_id = ? ORDER BY id ASC",
            (thread_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def count(thread_id: str) -> int:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM published_drafts WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    return row["cnt"] if row else 0


def get_all_drafts(limit: int = 500) -> List[Dict[str, Any]]:
    """All published drafts across all threads, for the dashboard. Newest first."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT thread_id, article_id, draft, published_at, created_at FROM published_drafts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_versions(limit: int = 1000) -> List[Dict[str, Any]]:
    """All draft versions across all threads, for the dashboard. Newest first."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT thread_id, draft, source, created_at FROM draft_versions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


init_db()
