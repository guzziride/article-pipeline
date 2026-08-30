from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import cost_tracker
import draft_store
import scheduled_store
import style_profile


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _week_key(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "unknown"
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def cost_summary(days: int = 30) -> Dict[str, Any]:
    daily = cost_tracker.get_daily_cost(days=days)
    total = sum(d["total_cost_usd"] for d in daily)
    by_node: Dict[str, float] = {}
    by_provider: Dict[str, float] = {}
    for d in daily:
        for node, cost in d["by_node"].items():
            by_node[node] = by_node.get(node, 0.0) + cost
        for prov, cost in d["by_provider"].items():
            by_provider[prov] = by_provider.get(prov, 0.0) + cost
    return {
        "days": days,
        "total_cost_usd": round(total, 6),
        "call_count": sum(d["call_count"] for d in daily),
        "daily": daily,
        "by_node": {k: round(v, 6) for k, v in sorted(by_node.items(), key=lambda x: -x[1])},
        "by_provider": {k: round(v, 6) for k, v in sorted(by_provider.items(), key=lambda x: -x[1])},
    }


def run_summary(days: int = 30) -> Dict[str, Any]:
    cutoff = _cutoff_iso(days)
    runs = scheduled_store.list_all_runs(limit=500)
    recent = [r for r in runs if (r.get("triggered_at") or "") >= cutoff]
    if not recent:
        return {"days": days, "total_runs": 0, "weekly": [], "by_topic": {}}
    by_week: Dict[str, Dict[str, Any]] = {}
    by_topic: Dict[str, int] = {}
    emailed = 0
    reviewed = 0
    total_candidates = 0
    for r in recent:
        wk = _week_key(r.get("triggered_at") or "")
        entry = by_week.setdefault(wk, {
            "week": wk, "runs": 0, "emailed": 0, "reviewed": 0, "candidates": 0,
        })
        entry["runs"] += 1
        entry["emailed"] += 1 if r.get("email_sent_at") else 0
        entry["reviewed"] += 1 if r.get("reviewed_at") else 0
        entry["candidates"] += r.get("candidate_count", 0)
        topic = (r.get("topic") or "untitled").strip()
        by_topic[topic] = by_topic.get(topic, 0) + 1
        emailed += 1 if r.get("email_sent_at") else 0
        reviewed += 1 if r.get("reviewed_at") else 0
        total_candidates += r.get("candidate_count", 0)
    weekly = sorted(by_week.values(), key=lambda x: x["week"])
    for w in weekly:
        w["avg_candidates"] = round(w["candidates"] / w["runs"], 1) if w["runs"] else 0
        w["email_rate"] = round(w["emailed"] / w["runs"] * 100, 0) if w["runs"] else 0
        w["review_rate"] = round(w["reviewed"] / w["runs"] * 100, 0) if w["runs"] else 0
    return {
        "days": days,
        "total_runs": len(recent),
        "total_emailed": emailed,
        "total_reviewed": reviewed,
        "total_candidates": total_candidates,
        "avg_candidates_per_run": round(total_candidates / len(recent), 1),
        "email_rate": round(emailed / len(recent) * 100, 0),
        "review_rate": round(reviewed / len(recent) * 100, 0),
        "weekly": weekly,
        "by_topic": dict(sorted(by_topic.items(), key=lambda x: -x[1])),
    }


def draft_summary(days: int = 30) -> Dict[str, Any]:
    cutoff = _cutoff_iso(days)
    drafts = draft_store.get_all_drafts(limit=500)
    versions = draft_store.get_all_versions(limit=1000)
    recent_drafts = [d for d in drafts if (d.get("created_at") or "") >= cutoff]
    recent_versions = [v for v in versions if (v.get("created_at") or "") >= cutoff]
    refine_count = sum(1 for v in recent_versions if (v.get("source") or "").startswith("refine:"))
    author_count = sum(1 for v in recent_versions if (v.get("source") or "") == "author")
    manual_count = sum(1 for v in recent_versions if (v.get("source") or "") == "manual_edit")
    avg_length = 0
    if recent_drafts:
        avg_length = round(sum(len(d.get("draft", "")) for d in recent_drafts) / len(recent_drafts), 0)
    by_week: Dict[str, int] = {}
    for d in recent_drafts:
        wk = _week_key(d.get("created_at") or "")
        by_week[wk] = by_week.get(wk, 0) + 1
    weekly = [{"week": wk, "published": cnt} for wk, cnt in sorted(by_week.items())]
    return {
        "days": days,
        "total_published": len(recent_drafts),
        "avg_draft_length": avg_length,
        "total_versions": len(recent_versions),
        "refine_count": refine_count,
        "author_count": author_count,
        "manual_edit_count": manual_count,
        "weekly": weekly,
    }


def topic_distribution(days: int = 30) -> Dict[str, Any]:
    summary = run_summary(days=days)
    return {"days": days, "topics": summary["by_topic"]}


def style_rule_usage() -> Dict[str, Any]:
    rules = style_profile.list_rules(include_disabled=True)
    active = [r for r in rules if not r["disabled"]]
    disabled = [r for r in rules if r["disabled"]]
    return {
        "total_rules": len(rules),
        "active_rules": len(active),
        "disabled_rules": len(disabled),
        "rules": sorted(rules, key=lambda r: -r["applied_count"]),
    }


def build_dashboard(days: int = 30) -> Dict[str, Any]:
    return {
        "days": days,
        "cost": cost_summary(days=days),
        "runs": run_summary(days=days),
        "drafts": draft_summary(days=days),
        "topics": topic_distribution(days=days),
        "style_rules": style_rule_usage(),
    }