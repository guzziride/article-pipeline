import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("SCHEDULED_RUNS_DB_PATH", os.path.join(os.path.dirname(__file__), "scheduled_runs.db"))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                triggered_at TEXT NOT NULL DEFAULT '',
                candidates_json TEXT NOT NULL DEFAULT '[]',
                email_sent_at TEXT DEFAULT NULL,
                reviewed_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_runs_triggered ON scheduled_runs(triggered_at)")
        conn.commit()


def store_run(run_id: str, thread_id: str, topic: str, candidates: List[Dict[str, Any]]) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO scheduled_runs (run_id, thread_id, topic, triggered_at, candidates_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, thread_id, topic, datetime.now(timezone.utc).isoformat(), json.dumps(candidates)),
        )
        conn.commit()


def mark_email_sent(run_id: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE scheduled_runs SET email_sent_at = ? WHERE run_id = ?",
            (datetime.now(timezone.utc).isoformat(), run_id),
        )
        conn.commit()


def mark_reviewed(run_id: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE scheduled_runs SET reviewed_at = ? WHERE run_id = ?",
            (datetime.now(timezone.utc).isoformat(), run_id),
        )
        conn.commit()


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM scheduled_runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["candidates"] = json.loads(d.get("candidates_json", "[]"))
    except json.JSONDecodeError:
        d["candidates"] = []
    return d


def list_runs(limit: int = 20) -> List[Dict[str, Any]]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT run_id, thread_id, topic, triggered_at, email_sent_at, reviewed_at FROM scheduled_runs ORDER BY triggered_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_candidates(run_id: str) -> List[Dict[str, Any]]:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT candidates_json FROM scheduled_runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return []
    try:
        return json.loads(row["candidates_json"])
    except json.JSONDecodeError:
        return []


init_db()
