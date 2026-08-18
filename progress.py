import threading
from typing import Dict, Optional

_store: Dict[str, Dict[str, object]] = {}
_lock = threading.Lock()


def set(thread_id: str, key: str, value: object) -> None:
    with _lock:
        state = _store.setdefault(thread_id, {})
        state[key] = value


def get(thread_id: str, key: str, default: object = None) -> object:
    with _lock:
        return _store.get(thread_id, {}).get(key, default)


def get_all(thread_id: str) -> Dict[str, object]:
    with _lock:
        return dict(_store.get(thread_id, {}))


def clear(thread_id: str) -> None:
    with _lock:
        _store.pop(thread_id, None)


def init_scout(thread_id: str, total_sources: int) -> None:
    set(thread_id, "phase", "scout")
    set(thread_id, "total_sources", total_sources)
    set(thread_id, "completed_sources", 0)
    set(thread_id, "pct", 0)
    set(thread_id, "message", "Starting scout...")
    set(thread_id, "source_log", [])
    set(thread_id, "source_results", [])


def advance_scout(thread_id: str, source_label: str, pct: int, message: str) -> None:
    completed = (get(thread_id, "completed_sources") or 0) + 1
    set(thread_id, "completed_sources", completed)
    set(thread_id, "pct", pct)
    set(thread_id, "message", message)
    set(thread_id, "current_source", source_label)
    set(thread_id, "phase", "scout")
    source_log: list = list(get(thread_id, "source_log") or [])
    source_log.append(source_label)
    set(thread_id, "source_log", source_log)


def set_phase(thread_id: str, phase: str, pct: int, message: str) -> None:
    set(thread_id, "phase", phase)
    set(thread_id, "pct", pct)
    set(thread_id, "message", message)


def record_source_result(thread_id: str, domain: str, kept: int, total: int, mode: str, drops: dict = None) -> None:
    existing = list(get(thread_id, "source_results") or [])
    existing.append({
        "domain": domain,
        "kept": kept,
        "total": total,
        "mode": mode,
        "drops": drops or {},
    })
    set(thread_id, "source_results", existing)


def append_stream_token(thread_id: str, token: str) -> None:
    with _lock:
        state = _store.setdefault(thread_id, {})
        tokens = list(state.get("stream_tokens") or [])
        tokens.append(token)
        state["stream_tokens"] = tokens
        state["stream_done"] = False


def get_stream_tokens(thread_id: str) -> list:
    with _lock:
        return list(_store.get(thread_id, {}).get("stream_tokens") or [])


def mark_stream_done(thread_id: str) -> None:
    set(thread_id, "stream_done", True)


def is_stream_done(thread_id: str) -> bool:
    return bool(get(thread_id, "stream_done", False))


def clear_stream(thread_id: str) -> None:
    with _lock:
        state = _store.get(thread_id)
        if state:
            state.pop("stream_tokens", None)
            state.pop("stream_done", None)
