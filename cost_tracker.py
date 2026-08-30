import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("COST_TRACKER_DB_PATH", os.path.join(os.path.dirname(__file__), "costs.db"))
_lock = threading.Lock()

# Approximate USD per 1M tokens (input, output). Not billing-accurate — for relative
# cost visibility only. Unrecognized provider/model pairs fall back to _DEFAULT_RATE.
PRICING: Dict[str, Dict[str, tuple]] = {
    "openai": {
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
    },
    "gemini": {
        "gemini-3.6-flash": (0.10, 0.40),
        "gemini-2.5-flash": (0.10, 0.40),
        "gemini-2.5-pro": (1.25, 5.00),
    },
    "groq": {
        "llama-3.1-8b-instant": (0.05, 0.08),
        "llama-3.1-70b-versatile": (0.59, 0.79),
    },
    "ollama": {},  # local inference — always free
}
_DEFAULT_RATE = (1.00, 3.00)  # conservative fallback for unrecognized models


def _rate(provider: str, model: str) -> tuple:
    provider = (provider or "").lower().strip()
    model = (model or "").strip()
    if provider == "ollama":
        return (0.0, 0.0)
    return PRICING.get(provider, {}).get(model, _DEFAULT_RATE)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                node TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                estimated INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def log_usage(
    thread_id: str,
    node: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated: bool,
) -> float:
    input_rate, output_rate = _rate(provider, model)
    cost_usd = (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO usage (ts, thread_id, node, provider, model, input_tokens, output_tokens, cost_usd, estimated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                thread_id or "",
                node,
                (provider or "").lower().strip(),
                (model or "").strip(),
                int(input_tokens),
                int(output_tokens),
                cost_usd,
                1 if estimated else 0,
            ),
        )
        conn.commit()
    return cost_usd


def get_thread_cost(thread_id: str) -> Dict[str, Any]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT node, input_tokens, output_tokens, cost_usd, estimated FROM usage WHERE thread_id = ? ORDER BY id ASC",
            (thread_id,),
        ).fetchall()

    by_node: Dict[str, Dict[str, Any]] = {}
    total_cost = 0.0
    any_estimated = False
    for r in rows:
        node = r["node"]
        entry = by_node.setdefault(
            node, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "estimated": False}
        )
        entry["input_tokens"] += r["input_tokens"]
        entry["output_tokens"] += r["output_tokens"]
        entry["cost_usd"] += r["cost_usd"]
        entry["estimated"] = entry["estimated"] or bool(r["estimated"])
        total_cost += r["cost_usd"]
        any_estimated = any_estimated or bool(r["estimated"])

    return {
        "thread_id": thread_id,
        "total_cost_usd": total_cost,
        "estimated": any_estimated,
        "by_node": by_node,
    }


def get_recent_totals(hours: int = 24) -> Dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT cost_usd, estimated FROM usage WHERE ts >= ?",
            (since,),
        ).fetchall()

    total_cost = sum(r["cost_usd"] for r in rows)
    return {
        "hours": hours,
        "total_cost_usd": total_cost,
        "call_count": len(rows),
        "estimated": any(bool(r["estimated"]) for r in rows),
    }


def get_daily_cost(days: int = 30) -> List[Dict[str, Any]]:
    """Daily cost rollup for the dashboard: one row per day, grouped by node + provider."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT ts, node, provider, model, cost_usd, estimated FROM usage WHERE ts >= ? ORDER BY ts ASC",
            (since,),
        ).fetchall()
    by_day: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        day = (r["ts"] or "")[:10]
        entry = by_day.setdefault(day, {
            "date": day, "total_cost_usd": 0.0, "call_count": 0,
            "by_node": {}, "by_provider": {},
        })
        entry["total_cost_usd"] += r["cost_usd"]
        entry["call_count"] += 1
        node = r["node"]
        entry["by_node"].setdefault(node, 0.0)
        entry["by_node"][node] += r["cost_usd"]
        prov = r["provider"]
        entry["by_provider"].setdefault(prov, 0.0)
        entry["by_provider"][prov] += r["cost_usd"]
    return sorted(by_day.values(), key=lambda x: x["date"])


init_db()
