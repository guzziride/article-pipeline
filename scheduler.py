import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

import domain_store
import scheduled_store
import emailer
from graph import build_graph
from settings import get_default_topic

load_dotenv()

_scheduler: Optional[BackgroundScheduler] = None
_lock = threading.Lock()
_graph_app = None
_graph_lock = threading.Lock()


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _get_graph_app():
    global _graph_app
    with _graph_lock:
        if _graph_app is None:
            _graph_app = build_graph()
        return _graph_app


def run_scout_analyst_job(
    topic: str,
    include_domains: Optional[List[str]],
    analyst_provider: str,
    writer_provider: str,
    analyst_model: Optional[str],
    writer_model: Optional[str],
    persona: str,
    run_id: str,
    fmt: str = "post",
) -> List[Dict[str, Any]]:
    """Runs scout+analyst to the approval interrupt, stores the run for review
    in the Scheduled Runs panel, and emails a digest if there are candidates.
    Shared by the cron scheduler and the webhook trigger so both unattended
    entry points get the same review flow. Returns the curated candidates.
    """
    thread_id = run_id
    app = _get_graph_app()
    config = {"configurable": {"thread_id": thread_id}}

    app.invoke(
        {
            "topic": topic,
            "include_domains": include_domains,
            "analyst_provider": analyst_provider,
            "writer_provider": writer_provider,
            "analyst_model": analyst_model,
            "writer_model": writer_model,
            "persona": persona,
            "format": fmt,
            "thread_id": thread_id,
        },
        config=config,
    )

    state = app.get_state(config)
    values = state.values if isinstance(state.values, dict) else {}
    candidates = values.get("curated_candidates", [])

    scheduled_store.store_run(run_id, thread_id, topic, candidates)

    if candidates:
        sent = emailer.send_digest(candidates, run_id, topic)
        if sent:
            scheduled_store.mark_email_sent(run_id)

    return candidates


def _run_scheduled_job() -> None:
    topic = _env("SCHEDULER_TOPIC", get_default_topic())
    stored_domains = domain_store.get_enabled_domains()
    if stored_domains:
        include_domains = stored_domains
    else:
        domains_raw = _env("SCHEDULER_DOMAINS", "")
        include_domains = [d.strip().lower() for d in domains_raw.split(",") if d.strip()] if domains_raw else None
    analyst_provider = _env("SCHEDULER_ANALYST_PROVIDER", "ollama")
    writer_provider = _env("SCHEDULER_WRITER_PROVIDER", "ollama")
    analyst_model = _env("SCHEDULER_ANALYST_MODEL") or None
    writer_model = _env("SCHEDULER_WRITER_MODEL") or None
    persona = _env("SCHEDULER_PERSONA", "cto_phd")
    fmt = _env("SCHEDULER_FORMAT", "post")

    now = datetime.now(timezone.utc)
    run_id = f"scheduled-{now.strftime('%Y-%m-%d-%H%M')}"

    try:
        run_scout_analyst_job(
            topic, include_domains, analyst_provider, writer_provider,
            analyst_model, writer_model, persona, run_id, fmt,
        )
    except Exception:
        return


def start_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        enabled = _env("SCHEDULER_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return
        cron_expr = _env("SCHEDULER_CRON", "0 7 * * 1-5")
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(_run_scheduled_job, CronTrigger.from_crontab(cron_expr), id="scheduled_scout")
        _scheduler.start()


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None


def is_running() -> bool:
    with _lock:
        return _scheduler is not None and _scheduler.running
