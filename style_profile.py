import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("STYLE_PROFILE_DB_PATH", os.path.join(os.path.dirname(__file__), "style_profile.db"))
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
            CREATE TABLE IF NOT EXISTS style_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona TEXT NOT NULL,
                rule_text TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL,
                applied_count INTEGER NOT NULL DEFAULT 0,
                disabled INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()


def add_rule(rule_text: str, persona: str = "*", source: str = "manual") -> Dict[str, Any]:
    rule_text = (rule_text or "").strip()
    if not rule_text:
        raise ValueError("rule_text must not be empty")
    persona = (persona or "*").strip()
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO style_rules (persona, rule_text, source, created_at) VALUES (?, ?, ?, ?)",
            (persona, rule_text, source, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, persona, rule_text, source, created_at, applied_count, disabled FROM style_rules WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return _row_to_dict(row)


def list_rules(include_disabled: bool = False) -> List[Dict[str, Any]]:
    with _lock, _conn() as conn:
        if include_disabled:
            rows = conn.execute("SELECT * FROM style_rules ORDER BY id ASC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM style_rules WHERE disabled = 0 ORDER BY id ASC"
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_active_rules(persona: Optional[str] = None) -> List[Dict[str, Any]]:
    persona = (persona or "*").strip()
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM style_rules WHERE disabled = 0 AND (persona = '*' OR persona = ?) ORDER BY id ASC",
            (persona,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_rule(rule_id: int, rule_text: Optional[str] = None, persona: Optional[str] = None) -> Optional[Dict[str, Any]]:
    fields = []
    params: List[Any] = []
    if rule_text is not None:
        rule_text = rule_text.strip()
        if not rule_text:
            raise ValueError("rule_text must not be empty")
        fields.append("rule_text = ?")
        params.append(rule_text)
    if persona is not None:
        fields.append("persona = ?")
        params.append(persona.strip() or "*")
    if not fields:
        return get_rule(rule_id)
    params.append(rule_id)
    with _lock, _conn() as conn:
        conn.execute(f"UPDATE style_rules SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM style_rules WHERE id = ?", (rule_id,)).fetchone()
    return _row_to_dict(row) if row else None


def set_disabled(rule_id: int, disabled: bool) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE style_rules SET disabled = ? WHERE id = ?",
            (1 if disabled else 0, rule_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_rule(rule_id: int) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM style_rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cur.rowcount > 0


def get_rule(rule_id: int) -> Optional[Dict[str, Any]]:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM style_rules WHERE id = ?", (rule_id,)).fetchone()
    return _row_to_dict(row) if row else None


def increment_applied(rule_ids: List[int]) -> None:
    if not rule_ids:
        return
    placeholders = ",".join("?" * len(rule_ids))
    with _lock, _conn() as conn:
        conn.execute(
            f"UPDATE style_rules SET applied_count = applied_count + 1 WHERE id IN ({placeholders})",
            rule_ids,
        )
        conn.commit()


def active_rules_block(persona: Optional[str] = None) -> Optional[str]:
    rules = get_active_rules(persona)
    if not rules:
        return None
    lines = [f"- {r['rule_text']}" for r in rules]
    return "Standing style rules:\n" + "\n".join(lines)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "persona": row["persona"],
        "rule_text": row["rule_text"],
        "source": row["source"],
        "created_at": row["created_at"],
        "applied_count": row["applied_count"],
        "disabled": bool(row["disabled"]),
    }


init_db()