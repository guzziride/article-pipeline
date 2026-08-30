import os
import asyncio
import json as _json
import secrets
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

import progress as progress_tracker
import draft_store
import domain_store
import scheduled_store
import scheduler
import cost_tracker
import style_profile
import dashboard
from graph import DEFAULT_PERSONA, DEFAULT_FORMAT, PERSONAS, FORMATS, _capture_usage, _get_chat_model, _resolve_model_name, build_graph
from settings import get_default_topic, get_ollama_model_options


load_dotenv()

web_app = FastAPI(title="Article Pipeline UI")
web_app.mount("/static", StaticFiles(directory="static"), name="static")
graph_app = build_graph()
RUN_HISTORY: Dict[str, List[Dict[str, Any]]] = {}

# Must comfortably exceed LLM_REQUEST_TIMEOUT (and its retries) or the SSE stream
# will report {"done": true} while the author/factuality calls are still running.
SSE_POLL_INTERVAL_SECONDS = 0.15
SSE_STREAM_TIMEOUT_SECONDS = int(os.getenv("SSE_STREAM_TIMEOUT_SECONDS", "240"))


class StartRequest(BaseModel):
    thread_id: str = Field(default="web-demo-thread")
    topic: str = Field(default_factory=get_default_topic)
    analyst_provider: str = Field(default="ollama")
    writer_provider: str = Field(default="ollama")
    analyst_model: Optional[str] = Field(default=None)
    writer_model: Optional[str] = Field(default=None)
    persona: str = Field(default="cto_phd")
    format: str = Field(default="post")
    include_domains: Optional[List[str]] = Field(default=None)


class ResumeRequest(BaseModel):
    thread_id: str = Field(...)
    selected_article_id: Optional[str] = Field(default=None)
    human_feedback: Optional[str] = Field(default=None)

    # Draft review flow
    action: Optional[str] = Field(default=None)
    edited_draft: Optional[str] = Field(default=None)


class RefineRequest(BaseModel):
    thread_id: str = Field(...)
    instruction: str = Field(...)
    current_draft: str = Field(...)


class ProviderHealthRequest(BaseModel):
    analyst_provider: str = Field(...)
    writer_provider: str = Field(...)
    analyst_model: Optional[str] = Field(default=None)
    writer_model: Optional[str] = Field(default=None)


class DomainsRequest(BaseModel):
    domains: List[str] = Field(default_factory=list)
    disabled: List[str] = Field(default_factory=list)


class WebhookStartRequest(BaseModel):
    topic: Optional[str] = Field(default=None)
    include_domains: Optional[List[str]] = Field(default=None)
    analyst_provider: Optional[str] = Field(default=None)
    writer_provider: Optional[str] = Field(default=None)
    analyst_model: Optional[str] = Field(default=None)
    writer_model: Optional[str] = Field(default=None)
    persona: Optional[str] = Field(default=None)
    format: Optional[str] = Field(default=None)


class StyleRuleRequest(BaseModel):
    rule_text: str = Field(...)
    persona: str = Field(default="*")
    source: str = Field(default="manual")


class StyleRuleUpdateRequest(BaseModel):
    rule_text: Optional[str] = Field(default=None)
    persona: Optional[str] = Field(default=None)


def _config(thread_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _state_snapshot(thread_id: str) -> Dict[str, Any]:
    state = graph_app.get_state(_config(thread_id))
    values = state.values if isinstance(state.values, dict) else {}
    return {
        "thread_id": thread_id,
        "next": list(state.next) if state.next else [],
        "workflow_status": values.get("workflow_status"),
        "include_domains": values.get("include_domains", []),
        "analyst_provider": values.get("analyst_provider"),
        "writer_provider": values.get("writer_provider"),
        "analyst_model": values.get("analyst_model"),
        "writer_model": values.get("writer_model"),
        "persona": values.get("persona"),
        "format": values.get("format"),
        "raw_articles": values.get("raw_articles", []),
        "scout_debug": values.get("scout_debug", {}),
        "curated_candidates": values.get("curated_candidates", []),
        "selected_article_id": values.get("selected_article_id"),
        "human_feedback": values.get("human_feedback"),
        "final_draft": values.get("final_draft"),
        "published_drafts": draft_store.get_drafts(thread_id),
        "draft_versions": draft_store.get_versions(thread_id),
    }


def _record_history(
    thread_id: str,
    action: str,
    state: Dict[str, Any],
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    entries = RUN_HISTORY.setdefault(thread_id, [])
    entries.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "workflow_status": state.get("workflow_status"),
            "next": state.get("next", []),
            "selected_article_id": state.get("selected_article_id"),
            "candidate_count": len(state.get("curated_candidates", [])),
            "has_final_draft": bool(state.get("final_draft")),
            "payload": payload or {},
            "draft_preview": (state.get("final_draft") or "")[:260],
        }
    )


def _history_for(thread_id: str) -> List[Dict[str, Any]]:
    return list(reversed(RUN_HISTORY.get(thread_id, [])))


def _check_provider_health(provider: str, model: Optional[str]) -> Dict[str, Any]:
    normalized_provider = (provider or "").strip().lower()
    chosen_model = (model or "").strip() or None
    try:
        llm = _get_chat_model(normalized_provider, role="healthcheck", model_override=chosen_model)
        response = llm.invoke("Reply with exactly: OK")
        content = str(getattr(response, "content", "")).strip()
        return {
            "provider": normalized_provider,
            "model": chosen_model,
            "ok": True,
            "detail": content[:200] or "OK",
        }
    except Exception as exc:
        return {
            "provider": normalized_provider,
            "model": chosen_model,
            "ok": False,
            "detail": str(exc),
        }


@web_app.get("/", response_class=HTMLResponse)
def index() -> str:
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" type="image/svg+xml" href="/favicon.ico" />
  <title>Article Pipeline HITL</title>
  <style>
    :root {
      --bg: #f4f7fb;
      --panel: #ffffff;
      --ink: #1b2430;
      --muted: #5f6b7a;
      --accent: #0e7490;
      --line: #d7dee8;
    }
    * { box-sizing: border-box; }
    html { max-width: 100%; overflow-x: hidden; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top right, #d8ecf8 0%, var(--bg) 45%);
      max-width: 100%;
      overflow-x: hidden;
    }
    .wrap {
      width: min(1080px, 100%);
      margin: 24px auto;
      padding: 0 16px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      min-width: 0;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(19, 33, 68, 0.06);
      min-width: 0;
    }
    h1 { margin: 0 0 10px; font-size: 24px; }
    h2 { margin: 0 0 10px; font-size: 18px; }
    p, label { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
      gap: 10px;
    }
    input, select, textarea, button {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      font: inherit;
    }
    textarea { min-height: 90px; resize: vertical; }
    button {
      background: var(--accent);
      color: #fff;
      border: none;
      cursor: pointer;
      transition: transform 120ms ease, opacity 120ms ease, box-shadow 140ms ease;
      box-shadow: 0 2px 8px rgba(14, 116, 144, 0.2);
    }
    button:active { transform: translateY(1px) scale(0.995); }
    button:disabled { opacity: 0.65; cursor: not-allowed; }
    button.is-loading {
      opacity: 0.85;
      box-shadow: 0 0 0 2px rgba(14, 116, 144, 0.2);
    }
    button.alt { background: #334155; }
    .progress-wrap {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      background: #f8fbff;
      margin-top: 10px;
    }
    .progress-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(120px, 100%), 1fr));
      gap: 8px;
    }
    .step {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      text-align: center;
      font-size: 12px;
      color: #475569;
      background: #fff;
    }
    .step.active {
      border-color: #0891b2;
      color: #0c4a6e;
      background: #ecfeff;
      font-weight: 600;
    }
    .step.done {
      border-color: #16a34a;
      color: #166534;
      background: #ecfdf5;
    }
    .row { display: flex; gap: 10px; flex-wrap: wrap; }
    .row > * { flex: 1 1 180px; min-width: 0; }
    a { overflow-wrap: anywhere; word-break: break-word; }
    .pill {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 99px;
      background: #ecfeff;
      color: #155e75;
      font-size: 13px;
      border: 1px solid #bae6fd;
    }
    .candidate {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      min-width: 0;
      cursor: pointer;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .candidate:hover { border-color: var(--accent); }
    .candidate.selected {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(14, 116, 144, 0.25);
      background: #f0f9ff;
    }
    .candidate-radio {
      width: 18px;
      height: 18px;
      margin-right: 8px;
      accent-color: var(--accent);
      flex-shrink: 0;
    }
    .raw-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      background: #f8fafc;
      min-width: 0;
    }
    pre {
      white-space: pre-wrap;
      background: #0f172a;
      color: #dbeafe;
      padding: 12px;
      border-radius: 10px;
      overflow: auto;
      max-width: 100%;
    }
    .muted { color: var(--muted); font-size: 14px; }
    .history-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      background: #fbfdff;
      min-width: 0;
    }
    .history-meta {
      font-family: "IBM Plex Mono", "Consolas", monospace;
      color: #334155;
      font-size: 12px;
      margin-top: 6px;
    }
    .audit-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      background: #fff7ed;
      min-width: 0;
    }
    .domain-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
      gap: 8px;
      margin-top: 8px;
    }
    .domain-item {
      display: flex;
      gap: 8px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      background: #fcfdff;
    }
    .domain-item input[type="checkbox"] {
      width: 18px;
      height: 18px;
      margin: 0;
      accent-color: var(--accent);
      flex-shrink: 0;
    }
    .domain-item code {
      font-family: "IBM Plex Mono", "Consolas", monospace;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    #source-breakdown > div { flex-wrap: wrap; gap: 8px; }
    .progress-bar-wrap {
      height: 8px;
      background: #e2e8f0;
      border-radius: 99px;
      overflow: hidden;
      margin-top: 8px;
    }
    .progress-bar-fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #0e7490, #06b6d4);
      border-radius: 99px;
      transition: width 0.4s ease;
    }
    .progress-pct {
      font-size: 12px;
      color: #475569;
      margin-top: 4px;
    }
    #toast-container {
      position: fixed;
      bottom: 20px;
      right: 20px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      z-index: 1000;
      max-width: min(360px, calc(100vw - 40px));
    }
    .toast {
      padding: 12px 16px;
      border-radius: 10px;
      box-shadow: 0 8px 24px rgba(19, 33, 68, 0.18);
      font-size: 14px;
      line-height: 1.4;
      color: #fff;
      background: var(--ink);
      opacity: 0;
      transform: translateY(8px);
      transition: opacity 160ms ease, transform 160ms ease;
      cursor: pointer;
      word-break: break-word;
    }
    .toast.toast-visible { opacity: 1; transform: translateY(0); }
    .toast.toast-error { background: #b91c1c; }
    .toast.toast-success { background: #0f766e; }
    .toast.toast-info { background: #334155; }
  </style>
</head>
<body>
  <div id="toast-container"></div>
  <div class="wrap">
    <section class="card">
      <h1>Article Pipeline HITL Console</h1>
      <p>Start a run, inspect curated candidates, approve one article, and resume to generate the final LinkedIn draft.</p>
    </section>

    <section class="card">
      <h2>1) Start Flow</h2>
      <div class="grid">
        <div><label>Thread ID</label><input id="thread-id" value="web-demo-thread" /></div>
        <div><label>Analyst Provider</label>
          <select id="analyst-provider">
            <option value="gemini">gemini</option>
            <option value="openai">openai</option>
            <option value="groq">groq</option>
            <option value="ollama" selected>ollama</option>
          </select>
        </div>
        <div><label>Writer Provider</label>
          <select id="writer-provider">
            <option value="openai">openai</option>
            <option value="gemini">gemini</option>
            <option value="groq">groq</option>
            <option value="ollama" selected>ollama</option>
          </select>
        </div>
        <div><label>Analyst Model (optional override)</label><input id="analyst-model" list="ollama-models" placeholder="e.g. gemini-3.6-flash or llama3.1" /></div>
        <div><label>Writer Model (optional override)</label><input id="writer-model" list="ollama-models" placeholder="e.g. gpt-4o or llama3.1" /></div>
        <div><label>Persona</label>
          <select id="persona-select">
            <option value="cto_phd" selected>CTO / PhD (Technical Authority)</option>
            <option value="startup_founder">Startup Founder (Scrappy/Growth)</option>
            <option value="practitioner_engineer">Practitioner Engineer (Hands-On)</option>
          </select>
        </div>
        <div><label>Format</label>
          <select id="format-select">
            <option value="post" selected>Single Post</option>
            <option value="thread">Thread (5-7 posts)</option>
            <option value="carousel">Carousel (6-8 slides)</option>
          </select>
        </div>
      </div>
      <datalist id="ollama-models">
__OLLAMA_OPTIONS__
      </datalist>
      <datalist id="openai-models">
        <option value="gpt-4o">
        <option value="gpt-4o-mini">
        <option value="o1-preview">
        <option value="o1-mini">
      </datalist>
      <datalist id="gemini-models">
        <option value="gemini-3.6-flash">
        <option value="gemini-2.5-flash">
        <option value="gemini-2.5-pro">
      </datalist>
      <datalist id="groq-models">
        <option value="llama-3.1-8b-instant">
        <option value="llama-3.1-70b-versatile">
        <option value="llama-3.2-1b-preview">
        <option value="llama-3.2-3b-preview">
        <option value="mixtral-8x7b-32768">
      </datalist>
      <div style="margin-top:10px;">
        <label>Topic</label>
        <textarea id="topic">__DEFAULT_TOPIC__</textarea>
      </div>
      <div style="margin-top:10px;">
        <label>Source Domains (editable and persistent)</label>
        <div class="row" style="margin-top:8px;">
          <input id="new-domain" placeholder="Add a domain, e.g. semafor.com" />
          <button id="add-domain-btn" class="alt" type="button">Add Domain</button>
        </div>
        <div id="domain-list" class="domain-list"></div>
        <p class="muted" style="margin:8px 0 0;">Checked domains are included in scouting. Uncheck to exclude without deleting.</p>
      </div>
      <div class="row" style="margin-top:10px;">
        <button id="start-btn">Start Graph</button>
        <button id="test-providers-btn" class="alt">Test Providers</button>
        <button id="refresh-btn" class="alt">Refresh State</button>
        <button id="reset-btn" class="alt" style="background:#dc2626;">Reset Thread</button>
      </div>
      <details style="margin-top:14px;">
        <summary style="cursor:pointer; font-weight:bold;">Style Rules (persistent voice preferences)</summary>
        <p class="muted" style="margin:8px 0 0;">Plain-text rules injected into every author + refine prompt. Persona <code>*</code> applies to all; a specific persona only applies when selected. Rules are advisory — the LLM may deviate.</p>
        <div class="row" style="gap:6px; flex-wrap:wrap; margin-top:8px;">
          <input id="new-style-rule" placeholder="e.g. keep posts under 200 words, avoid exclamation marks" style="flex:1 1 240px;" />
          <select id="new-rule-persona" style="flex:0 0 auto; width:auto;">
            <option value="*">*</option>
            <option value="cto_phd">cto_phd</option>
            <option value="startup_founder">startup_founder</option>
            <option value="practitioner_engineer">practitioner_engineer</option>
          </select>
          <button id="add-rule-btn" class="alt" type="button" style="flex:0 0 auto;">Add Rule</button>
        </div>
        <div id="style-rules-list" class="muted" style="margin-top:8px;">No style rules yet.</div>
      </details>
      <div class="progress-wrap">
        <div id="activity-text" class="muted">Ready.</div>
        <div class="progress-row" style="margin-top:8px;">
          <div id="step-scout" class="step">Scout</div>
          <div id="step-analyst" class="step">Curate</div>
          <div id="step-approval" class="step">Await Approval</div>
          <div id="step-author" class="step">Author Draft</div>
          <div id="step-edit-approval" class="step">Draft Approval</div>
        </div>
        <div id="progress-section" style="display:none;">
          <div class="progress-bar-wrap"><div id="progress-fill" class="progress-bar-fill"></div></div>
          <div id="progress-pct" class="progress-pct">0%</div>
        </div>
      </div>
      <pre id="health-results" style="margin-top:10px;">Provider health checks not run yet.</pre>
    </section>

    <section class="card">
      <h2>2) Articles Retrieved by Source</h2>
      <p class="muted">Per-source breakdown of articles kept after scouting.</p>
      <div id="source-breakdown" class="muted">No source data yet.</div>
    </section>

    <section class="card">
      <h2>3) Curated Candidates (Approval Step)</h2>
      <div id="candidates" class="muted">No candidates yet.</div>
      <div class="grid" style="margin-top:10px;">
        <div><label>Selected Article ID</label><input id="selected-id" /></div>
      </div>
      <p class="muted" style="margin-top:8px;">You can choose an ID from curated candidates or any raw article in the section below.</p>
      <div style="margin-top:10px;">
        <label>Human Feedback (optional)</label>
        <textarea id="human-feedback" placeholder="Example: emphasize implications for SaaS pricing and GTM"></textarea>
      </div>
      <div class="row" style="margin-top:10px;">
        <button id="resume-btn">Approve + Generate Draft</button>
      </div>
    </section>

    <section class="card">
      <details>
        <summary style="cursor:pointer; font-weight:bold; font-size:18px;"><h2 style="display:inline; margin:0;">3b) Raw Articles (All Scout Results)</h2></summary>
        <div id="raw-articles" class="muted" style="margin-top:10px;">No raw articles yet.</div>
      </details>
    </section>

    <section class="card">
      <h2>4) Draft Review & Publish</h2>
      <div style="margin-bottom:10px;">
        <label>Quick Refine</label>
        <div class="row" style="gap:6px; flex-wrap:wrap; margin-top:6px;">
          <button id="refine-hook" class="alt" style="flex:0 0 auto; padding:6px 12px; font-size:13px;">Make Hook Punchier</button>
          <button id="refine-shorten" class="alt" style="flex:0 0 auto; padding:6px 12px; font-size:13px;">Shorten</button>
          <button id="refine-technical" class="alt" style="flex:0 0 auto; padding:6px 12px; font-size:13px;">More Technical</button>
          <button id="refine-cta" class="alt" style="flex:0 0 auto; padding:6px 12px; font-size:13px;">Stronger CTA</button>
          <button id="refine-grammar" class="alt" style="flex:0 0 auto; padding:6px 12px; font-size:13px;">Fix Grammar</button>
        </div>
      </div>
      <div style="margin-bottom:10px;">
        <label>Edit draft before approving</label>
        <textarea id="draft-editor" style="min-height:220px; font-family: 'IBM Plex Mono', 'Consolas', monospace; font-size:13px; white-space:pre-wrap;">No draft yet.</textarea>
      </div>
      <div class="row" style="gap:8px; flex-wrap:wrap;">
        <button id="approve-draft-btn">Approve & Publish</button>
        <button id="edit-draft-btn">Save Edit</button>
        <button id="pick-another-btn" class="alt">Pick Another Article</button>
        <button id="open-linkedin-btn" class="alt" style="background:#0a66c2;">Open LinkedIn Post</button>
        <button id="copy-draft-btn" class="alt">Copy Draft</button>
        <button id="download-txt-btn" class="alt">Download .txt</button>
        <button id="download-md-btn" class="alt">Download .md</button>
      </div>
      <p class="muted" style="margin-top:8px;">
        <strong>Approve & Publish</strong> — marks draft as published.<br/>
        <strong>Save Edit</strong> — saves your edits and re-enters draft review.<br/>
        <strong>Pick Another Article</strong> — go back to approval step (articles preserved).<br/>
        <strong>Open LinkedIn Post</strong> — opens LinkedIn composer with draft content.
      </p>
      <div id="factuality-notes" class="muted" style="margin-top:10px; padding:8px; background:#fffbeb; border:1px solid #fde68a; border-radius:8px; display:none;"></div>
      <div id="cost-summary" class="muted" style="margin-top:6px; font-size:13px; display:none;"></div>
      <div style="margin-top:14px;">
        <label>Hashtag Library</label>
        <div id="hashtag-chips" class="row" style="gap:6px; flex-wrap:wrap; margin-top:6px;"></div>
      </div>
      <details style="margin-top:14px;">
        <summary style="cursor:pointer; font-weight:bold;">Version History</summary>
        <div id="draft-versions" class="muted" style="margin-top:10px;">No versions yet.</div>
      </details>
      <h3 style="margin-top:14px;">Published Drafts (This Thread)</h3>
      <div id="published-drafts" class="muted">None published yet.</div>
    </section>

    <section class="card">
      <h2>Sources Queried</h2>
      <p class="muted">Each source is logged in real-time as it is queried.</p>
      <div id="source-log" class="muted">Waiting for scout to start...</div>
    </section>

    <section class="card">
      <h2>Live State JSON</h2>
      <pre id="state-json">{}</pre>
    </section>

    <section class="card">
      <details>
        <summary style="cursor:pointer; font-weight:bold; font-size:18px;"><h2 style="display:inline; margin:0;">Performance Dashboard</h2></summary>
        <p class="muted" style="margin-top:8px;">Aggregate views across all runs: cost, throughput, topics, and style rule usage. Read-only.</p>
        <div class="row" style="gap:8px; flex-wrap:wrap; margin-top:8px; align-items:center;">
          <label style="font-size:13px;">Days:</label>
          <input id="dashboard-days" type="number" value="30" min="1" max="365" style="width:70px;" />
          <button id="refresh-dashboard-btn" class="alt" type="button">Refresh Dashboard</button>
        </div>
        <div id="dashboard-content" class="muted" style="margin-top:10px;">No dashboard data loaded. Click Refresh.</div>
      </details>
    </section>

    <section class="card">
      <h2>Scout Debug</h2>
      <p class="muted">Use this to diagnose empty `raw_articles` and domain filter issues.</p>
      <div id="domain-diagnostics" class="muted" style="margin-bottom:10px;">No domain diagnostics yet.</div>
      <pre id="scout-debug" style="max-height:420px; overflow:auto;">No scout debug yet.</pre>
      <h2 style="margin-top:14px;">Dropped Articles Audit</h2>
      <p class="muted">Shows why records were excluded (for example `missing_publish_date`).</p>
      <div id="dropped-audit" class="muted">No dropped articles yet.</div>
    </section>

    <section class="card">
      <h2>Run History (Thread)</h2>
      <p class="muted">Tracks start/resume/checkpoint snapshots for this thread.</p>
      <div id="history" class="muted">No history yet.</div>
    </section>

    <section class="card">
      <h2>Scheduled Runs</h2>
      <p class="muted">Automated scout+analyst runs triggered by the scheduler. Click a run to load its candidates.</p>
      <div id="scheduled-runs" class="muted">Loading...</div>
    </section>
  </div>

  <script>
    const stateJson = document.getElementById("state-json");
    const scoutDebugEl = document.getElementById("scout-debug");
    const draftEditorEl = document.getElementById("draft-editor");
    const candidates = document.getElementById("candidates");
    const rawArticlesEl = document.getElementById("raw-articles");
    const historyEl = document.getElementById("history");
    const healthResultsEl = document.getElementById("health-results");
    const droppedAuditEl = document.getElementById("dropped-audit");
    const domainDiagnosticsEl = document.getElementById("domain-diagnostics");
    const activityTextEl = document.getElementById("activity-text");
    const stepScoutEl = document.getElementById("step-scout");
    const stepAnalystEl = document.getElementById("step-analyst");
    const stepApprovalEl = document.getElementById("step-approval");
    const stepAuthorEl = document.getElementById("step-author");
    const stepEditApprovalEl = document.getElementById("step-edit-approval");
    const progressSectionEl = document.getElementById("progress-section");
    const progressFillEl = document.getElementById("progress-fill");
    const progressPctEl = document.getElementById("progress-pct");
    const sourceLogEl = document.getElementById("source-log");

    const threadIdEl = document.getElementById("thread-id");
    const topicEl = document.getElementById("topic");
    const analystProviderEl = document.getElementById("analyst-provider");
    const writerProviderEl = document.getElementById("writer-provider");
    const analystModelEl = document.getElementById("analyst-model");
    const writerModelEl = document.getElementById("writer-model");
    const personaSelectEl = document.getElementById("persona-select");
    const formatSelectEl = document.getElementById("format-select");
    const newDomainEl = document.getElementById("new-domain");
    const domainListEl = document.getElementById("domain-list");
    const selectedIdEl = document.getElementById("selected-id");
    const feedbackEl = document.getElementById("human-feedback");
    let startProgressTimer = null;

    const DEFAULT_DOMAINS = [
      "arxiv.org",
      "bair.berkeley.edu",
      "deepmind.google",
      "openai.com",
      "anthropic.com",
      "infoq.com",
      "modelcontextprotocol.io",
      "langchain.com",
      "github.blog",
      "microsoft.com",
      "a16z.com",
      "stratechery.com",
      "theinformation.com",
      "venturebeat.com",
      "techcrunch.com",
      "news.ycombinator.com",
      "tldr.tech",
      "alphasignals.com",
      "importai.substack.com",
      "latent.space",
      "thelogic.co",
      "bctechassociation.org"
    ];
    const STORAGE_DOMAINS_KEY = "article_pipeline_domains";
    const STORAGE_DISABLED_KEY = "article_pipeline_domains_disabled";

    const HASHTAG_LIBRARY = [
      "#AgenticAI", "#MCP", "#SaaS", "#LLMOps", "#DistributedSystems",
      "#DevOps", "#SoftwareArchitecture", "#TechLeadership", "#MachineLearning",
      "#AIInfrastructure", "#Observability", "#PlatformEngineering", "#Kubernetes",
      "#OpenSource", "#EngineeringLeadership", "#ProductionAI",
    ];

    function normalizeDomain(value) {
      const raw = (value || "").trim().toLowerCase();
      if (!raw) return "";
      const noScheme = raw.replace(/^https?:[/][/]/, "");
      const hostAndPath = noScheme.split(/[?#]/)[0];
      const host = hostAndPath.split("/")[0].replace(/^www[.]/, "");
      return host;
    }

    function loadDomainsState() {
      let stored = [];
      let disabled = [];
      try {
        stored = JSON.parse(localStorage.getItem(STORAGE_DOMAINS_KEY) || "[]");
      } catch (_) {}
      try {
        disabled = JSON.parse(localStorage.getItem(STORAGE_DISABLED_KEY) || "[]");
      } catch (_) {}
      const merged = [...new Set([...DEFAULT_DOMAINS, ...stored.map(normalizeDomain).filter(Boolean)])];
      const disabledSet = new Set(disabled.map(normalizeDomain));
      return { domains: merged, disabledSet };
    }

    function pushDomainsToServer(domains, disabledSet) {
      fetch("/api/domains", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domains, disabled: [...disabledSet] }),
      }).catch(() => {});
    }

    function saveDomainsState(domains, disabledSet, opts) {
      localStorage.setItem(STORAGE_DOMAINS_KEY, JSON.stringify(domains));
      localStorage.setItem(STORAGE_DISABLED_KEY, JSON.stringify([...disabledSet]));
      if (!opts || !opts.skipServerSync) {
        pushDomainsToServer(domains, disabledSet);
      }
    }

    async function hydrateDomainsFromServer() {
      try {
        const res = await fetch("/api/domains");
        const data = await res.json();
        const local = loadDomainsState();
        if (!data.ok || !data.domains || !data.domains.length) {
          pushDomainsToServer(local.domains, local.disabledSet);
          return;
        }
        const merged = [...new Set([...local.domains, ...data.domains.map(normalizeDomain).filter(Boolean)])];
        const disabledSet = new Set((data.disabled || []).map(normalizeDomain));
        saveDomainsState(merged, disabledSet, { skipServerSync: true });
        renderDomainList();
      } catch (_) {}
    }

    function renderDomainList() {
      const { domains, disabledSet } = loadDomainsState();
      if (!domains.length) {
        domainListEl.innerHTML = "<span class='muted'>No domains configured.</span>";
        return;
      }
      domainListEl.innerHTML = domains.map((domain) => {
        const checked = disabledSet.has(domain) ? "" : "checked";
        return `
          <label class="domain-item">
            <input type="checkbox" data-domain="${domain}" ${checked} />
            <code>${domain}</code>
          </label>
        `;
      }).join("");
      domainListEl.querySelectorAll("input[type='checkbox']").forEach((el) => {
        el.addEventListener("change", (event) => {
          const target = event.target;
          const domain = normalizeDomain(target.getAttribute("data-domain") || "");
          const next = loadDomainsState();
          if (!target.checked) {
            next.disabledSet.add(domain);
          } else {
            next.disabledSet.delete(domain);
          }
          saveDomainsState(next.domains, next.disabledSet);
        });
      });
    }

    function addDomain() {
      const domain = normalizeDomain(newDomainEl.value);
      if (!domain) return;
      const state = loadDomainsState();
      if (!state.domains.includes(domain)) {
        state.domains.push(domain);
      }
      state.disabledSet.delete(domain);
      saveDomainsState(state.domains, state.disabledSet);
      newDomainEl.value = "";
      renderDomainList();
    }

    function getSelectedDomains() {
      const state = loadDomainsState();
      return state.domains.filter((domain) => !state.disabledSet.has(domain));
    }

    function syncDomainsFromState(serverDomains) {
      if (!serverDomains || !serverDomains.length) return;
      const state = loadDomainsState();
      let changed = false;
      serverDomains.map(normalizeDomain).filter(Boolean).forEach((domain) => {
        if (!state.domains.includes(domain)) {
          state.domains.push(domain);
          changed = true;
        }
      });
      if (changed) {
        saveDomainsState(state.domains, state.disabledSet);
      }
      renderDomainList();
    }

    function renderCandidates(items) {
      if (!items || !items.length) {
        candidates.innerHTML = "<span class='muted'>No candidates available.</span>";
        return;
      }
      const currentId = selectedIdEl.value.trim();
      const html = items.map((item) => {
        const safeTitle = (item.title || "Untitled").replace(/</g, "&lt;");
        const safeSummary = (item.summary || "").replace(/</g, "&lt;");
        const checked = currentId === String(item.id) ? "checked" : "";
        const selClass = currentId === String(item.id) ? " selected" : "";
        return `
          <div class="candidate${selClass}" data-article-id="${item.id}" onclick="pickId('${item.id}')">
            <div style="display:flex;align-items:flex-start;">
              <input type="radio" name="candidate-select" class="candidate-radio" value="${item.id}" ${checked} onclick="event.stopPropagation(); pickId('${item.id}')" />
              <div style="min-width:0;">
                <div><strong>[${item.id}] ${safeTitle}</strong></div>
                <div class="muted">Source: ${item.source || "unknown"} | Relevance: ${item.relevance_score ?? 0}</div>
                <div style="margin:8px 0;"><a href="${item.url}" target="_blank" onclick="event.stopPropagation()">${item.url}</a></div>
                <div class="muted">${safeSummary}</div>
              </div>
            </div>
          </div>
        `;
      }).join("");
      candidates.innerHTML = html;
    }

    function renderRawArticles(items) {
      if (!items || !items.length) {
        rawArticlesEl.innerHTML = "<span class='muted'>No raw articles available.</span>";
        return;
      }
      const html = items.map((item) => {
        const safeTitle = (item.title || "Untitled").replace(/</g, "&lt;");
        const safeSummary = (item.summary || "").replace(/</g, "&lt;");
        const published = (item.published_at || "unknown").replace(/</g, "&lt;");
        let hostname = "";
        try {
          hostname = item.url ? new URL(item.url).hostname.replace(/^www[.]/, "") : "";
        } catch (_) {}
        return `
          <div class="raw-item">
            <div><strong>[${item.id}] ${safeTitle}</strong></div>
            <div class="muted">Source: ${item.source || "unknown"} | Domain: ${hostname || "unknown"} | Published: ${published}</div>
            <div style="margin:8px 0;"><a href="${item.url}" target="_blank">${item.url}</a></div>
            <div class="muted">${safeSummary}</div>
            <div style="margin-top:8px;"><button onclick="pickId('${item.id}')">Use ID ${item.id}</button></div>
          </div>
        `;
      }).join("");
      rawArticlesEl.innerHTML = html;
    }

    function renderDomainDiagnostics(scoutDebug) {
      if (!scoutDebug || typeof scoutDebug !== "object") {
        domainDiagnosticsEl.innerHTML = "<span class='muted'>No domain diagnostics yet.</span>";
        return;
      }

      const mode = scoutDebug.effective_domain_mode || "unknown";
      const fallback = scoutDebug.used_domain_fallback ? "yes" : "no";
      let stats = scoutDebug.stats || {};
      if (stats.fallback_without_domains) {
        stats = stats.fallback_without_domains;
      }
      const topDomains = Array.isArray(stats.returned_domains_top) ? stats.returned_domains_top : [];
      const requested = Array.isArray(stats.requested_include_domains)
        ? stats.requested_include_domains
        : (Array.isArray(scoutDebug.include_domains) ? scoutDebug.include_domains : []);

      const top = topDomains.slice(0, 8).map((d) => `${d.domain} (${d.count})`).join(", ") || "none";
      const outside = Number(stats.kept_outside_requested_domains || 0);

      domainDiagnosticsEl.innerHTML = `
        <div><strong>Domain mode:</strong> <code>${mode}</code> | <strong>Fallback used:</strong> <code>${fallback}</code></div>
        <div style="margin-top:6px;"><strong>Requested include domains:</strong> ${requested.length ? requested.join(", ") : "(none)"}</div>
        <div style="margin-top:6px;"><strong>Top returned domains:</strong> ${top}</div>
        <div style="margin-top:6px;"><strong>Kept results outside requested domains:</strong> ${outside}</div>
      `;
    }

    function pickId(id) {
      selectedIdEl.value = id;
      document.querySelectorAll(".candidate").forEach(el => {
        el.classList.toggle("selected", el.getAttribute("data-article-id") === String(id));
      });
      document.querySelectorAll("input[name='candidate-select']").forEach(r => {
        r.checked = r.value === String(id);
      });
    }
    window.pickId = pickId;


    function setActivity(text) {
      activityTextEl.textContent = text || "Ready.";
    }

    function toast(message, type = "info", duration = 4000) {
      const container = document.getElementById("toast-container");
      const el = document.createElement("div");
      el.className = `toast toast-${type}`;
      el.textContent = message;
      el.addEventListener("click", () => el.remove());
      container.appendChild(el);
      requestAnimationFrame(() => el.classList.add("toast-visible"));
      setTimeout(() => {
        el.classList.remove("toast-visible");
        setTimeout(() => el.remove(), 200);
      }, duration);
    }

    function resetSteps() {
      [stepScoutEl, stepAnalystEl, stepApprovalEl, stepAuthorEl, stepEditApprovalEl].forEach((el) => {
        el.classList.remove("active", "done");
      });
    }

    function setFlowStep(step) {
      const order = ["scout", "analyst", "approval", "author", "edit_approval"];
      const elements = {
        scout: stepScoutEl,
        analyst: stepAnalystEl,
        approval: stepApprovalEl,
        author: stepAuthorEl,
        edit_approval: stepEditApprovalEl,
      };
      const idx = order.indexOf(step);
      resetSteps();
      if (idx < 0) return;
      order.forEach((name, i) => {
        if (i < idx) elements[name].classList.add("done");
        else if (i === idx) elements[name].classList.add("active");
      });
    }

    function stopStartProgressAnimation() {
      if (startProgressTimer) {
        clearInterval(startProgressTimer);
        startProgressTimer = null;
      }
      progressSectionEl.style.display = "none";
    }

    function pollProgress() {
      const threadId = threadIdEl.value.trim();
      if (!threadId) return;
      fetch(`/api/progress/${encodeURIComponent(threadId)}`)
        .then(r => r.json())
        .then(data => {
          if (!data.ok) return;
          const pct = data.pct || 0;
          progressFillEl.style.width = pct + "%";
          progressPctEl.textContent = pct + "%";
          if (data.message) {
            setActivity(data.message);
          }
          const log = data.source_log || [];
          if (log.length) {
            sourceLogEl.innerHTML = log.map(s => `<span class="pill">${s}</span>`).join(" ");
          } else if (data.phase === "scout") {
            sourceLogEl.innerHTML = `<span class="muted">Waiting for first source...</span>`;
          }
          const results = data.source_results || [];
          if (results.length) {
            renderLiveSourceBreakdown(results);
          }
          if (pct < 100) return;
          stopStartProgressAnimation();
        })
        .catch(() => {});
    }

    function renderLiveSourceBreakdown(results) {
      const html = results.map((s) => {
        const drops = s.drops || {};
        const tags = [];
        if (drops.dropped_topic_mismatch) tags.push(`topic_mismatch: ${drops.dropped_topic_mismatch}`);
        if (drops.dropped_missing_date) tags.push(`no_date: ${drops.dropped_missing_date}`);
        if (drops.dropped_old_date) tags.push(`too_old: ${drops.dropped_old_date}`);
        if (drops.dropped_no_title_url) tags.push(`no_title/url: ${drops.dropped_no_title_url}`);
        const tagHtml = tags.length ? `<span style="font-size:12px;color:#6b7280;"> (${tags.join(", ")})</span>` : "";
        return `<div style="padding:6px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;">
          <span><strong>${s.domain}</strong> <span class="muted">[${s.mode}]</span>${tagHtml}</span>
          <span><strong>${s.kept}</strong> / ${s.total} articles</span>
        </div>`;
      }).join("");
      document.getElementById("source-breakdown").innerHTML = html;
    }

    function startStartProgressAnimation() {
      stopStartProgressAnimation();
      progressSectionEl.style.display = "block";
      progressFillEl.style.width = "0%";
      progressPctEl.textContent = "0%";
      const states = [
        { step: "scout", text: "Scouting sources..." },
        { step: "analyst", text: "Curating top candidates..." },
      ];
      let i = 0;
      setFlowStep(states[i].step);
      setActivity(states[i].text);
      pollProgress();
      startProgressTimer = setInterval(pollProgress, 1200);
    }

    async function withBusyButton(buttonEl, busyLabel, fn) {
      const original = buttonEl.textContent;
      buttonEl.disabled = true;
      buttonEl.classList.add("is-loading");
      buttonEl.textContent = busyLabel;
      try {
        await fn();
      } finally {
        buttonEl.disabled = false;
        buttonEl.classList.remove("is-loading");
        buttonEl.textContent = original;
      }
    }

    function renderHistory(history) {
      if (!history || !history.length) {
        historyEl.innerHTML = "<span class='muted'>No history yet for this thread.</span>";
        return;
      }
      const html = history.map((item) => {
        const action = (item.action || "unknown").replace(/</g, "&lt;");
        const status = (item.workflow_status || "n/a").replace(/</g, "&lt;");
        const at = (item.at || "").replace(/</g, "&lt;");
        const selected = (item.selected_article_id || "-").replace(/</g, "&lt;");
        const preview = (item.draft_preview || "").replace(/</g, "&lt;");
        return `
          <div class="history-item">
            <div><strong>${action}</strong> · status: ${status}</div>
            <div class="history-meta">at=${at} | selected=${selected} | candidates=${item.candidate_count || 0} | final_draft=${item.has_final_draft ? "yes" : "no"}</div>
            ${preview ? `<div class="muted" style="margin-top:8px;">${preview}${preview.length >= 260 ? "..." : ""}</div>` : ""}
          </div>
        `;
      }).join("");
      historyEl.innerHTML = html;
    }

    function renderDroppedAudit(scoutDebug) {
      const stats = (scoutDebug && scoutDebug.stats) || {};
      const urlAudit = Array.isArray(stats.url_audit) ? stats.url_audit : [];
      const dropped = urlAudit.filter((item) => item && item.kept === false);
      if (!dropped.length) {
        droppedAuditEl.innerHTML = "<span class='muted'>No dropped articles in current scout run.</span>";
        return;
      }
      const html = dropped.map((item) => {
        const id = String(item.id || "-").replace(/</g, "&lt;");
        const title = String(item.title || "Untitled").replace(/</g, "&lt;");
        const url = String(item.url || "").replace(/</g, "&lt;");
        const reason = String(item.drop_reason || "unknown").replace(/</g, "&lt;");
        const publishedAt = String(item.published_at || "").replace(/</g, "&lt;");
        const source = String(item.published_at_source || "").replace(/</g, "&lt;");
        const rawFields = JSON.stringify(item.raw_date_fields || {}, null, 2).replace(/</g, "&lt;");
        return `
          <div class="audit-item">
            <div><strong>[${id}] ${title}</strong></div>
            <div class="muted" style="margin:6px 0;">Reason: <code>${reason}</code></div>
            <div class="muted">URL: <a href="${url}" target="_blank">${url}</a></div>
            <div class="muted">Parsed Published At: ${publishedAt || "(none)"}</div>
            <div class="muted">Published Source: ${source || "(none)"}</div>
            <details style="margin-top:6px;">
              <summary class="muted">Raw date fields</summary>
              <pre style="margin-top:8px; max-height:180px; overflow:auto;">${rawFields}</pre>
            </details>
          </div>
        `;
      }).join("");
      droppedAuditEl.innerHTML = html;
    }

    async function fetchState() {
      const threadId = threadIdEl.value.trim();
      if (!threadId) return;
      setActivity("Refreshing latest state...");
      const res = await fetch(`/api/state/${encodeURIComponent(threadId)}`);
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to fetch state");
      }
      applyState(data.state, data.history || []);
    }

    function applyState(state, history) {
      stopStartProgressAnimation();
      stateJson.textContent = JSON.stringify(state, null, 2);
      scoutDebugEl.textContent = JSON.stringify(state.scout_debug || {}, null, 2);
      renderDomainDiagnostics(state.scout_debug || {});

      const status = state.workflow_status || "";
      if (status === "scouted") {
        setFlowStep("analyst");
        setActivity("Scout completed. Curating candidates...");
      } else if (status === "awaiting_approval") {
        setFlowStep("approval");
        setActivity(`Ready for approval. ${ (state.curated_candidates || []).length } candidates available.`);
      } else if (status === "approved") {
        setFlowStep("author");
        setActivity("Generating final draft...");
      } else if (status === "awaiting_draft_approval") {
        setFlowStep("edit_approval");
        stepEditApprovalEl.classList.add("active");
        setActivity("Draft ready. Review, edit, approve, or pick another article.");
      } else if (status === "published") {
        setFlowStep("edit_approval");
        [stepAuthorEl, stepEditApprovalEl].forEach((el) => {
          el.classList.remove("active");
          el.classList.add("done");
        });
        setActivity("Draft published. Pick another article or finish.");
      } else if (status === "no_recent_articles") {
        setFlowStep("scout");
        setActivity("No recent articles found for current filters.");
      }

      renderCandidates(state.curated_candidates || []);
      renderRawArticles(state.raw_articles || []);
      renderDroppedAudit(state.scout_debug || {});
      renderHistory(history || []);
      if (state.selected_article_id) {
        selectedIdEl.value = state.selected_article_id;
      } else {
        selectedIdEl.value = "";
      }
      if (state.analyst_model) {
        analystModelEl.value = state.analyst_model;
      }
      if (state.writer_model) {
        writerModelEl.value = state.writer_model;
      }
      personaSelectEl.value = state.persona || "cto_phd";
      formatSelectEl.value = state.format || "post";
      syncDomainsFromState(state.include_domains || []);
      const draftContent = state.final_draft || "";
      if (draftContent) {
        draftEditorEl.value = draftContent;
      } else {
        draftEditorEl.value = "";
      }
      renderHashtagChips();
      CURRENT_DRAFT_VERSIONS = state.draft_versions || [];
      renderDraftVersions(CURRENT_DRAFT_VERSIONS);
      renderPublishedDrafts(state.published_drafts || []);
      const factualityEl = document.getElementById("factuality-notes");
      const notes = (state.scout_debug && state.scout_debug.factuality_notes) || "";
      if (notes && notes !== "All claims verified.") {
        factualityEl.style.display = "block";
        factualityEl.innerHTML = "<strong>Factuality Check:</strong> " + notes.replace(/</g, "&lt;");
      } else {
        factualityEl.style.display = "none";
      }
      refreshCostSummary();
    }

    async function refreshCostSummary() {
      const threadId = threadIdEl.value.trim();
      const costEl = document.getElementById("cost-summary");
      if (!threadId) {
        costEl.style.display = "none";
        return;
      }
      try {
        const res = await fetch(`/api/costs/${encodeURIComponent(threadId)}`);
        const data = await res.json();
        if (!res.ok || !data.total_cost_usd) {
          costEl.style.display = "none";
          return;
        }
        const prefix = data.estimated ? "~" : "";
        const parts = Object.entries(data.by_node || {}).map(
          ([node, v]) => `${node} ${v.estimated ? "~" : ""}$${v.cost_usd.toFixed(4)}`
        );
        costEl.textContent = `Est. cost this thread: ${prefix}$${data.total_cost_usd.toFixed(4)}` +
          (parts.length ? ` (${parts.join(", ")})` : "");
        costEl.style.display = "block";
      } catch (_) {
        costEl.style.display = "none";
      }
    }

    function renderSourceBreakdown(scoutDebug) {
      const sources = (scoutDebug && scoutDebug.sources) || [];
      if (!sources.length) {
        document.getElementById("source-breakdown").innerHTML = "<span class='muted'>No source data yet.</span>";
        return;
      }
      const html = sources.map((s) => {
        const domain = s.requested_domain || s.domain || "unknown";
        const kept = s.kept_count || 0;
        const total = s.entries_count || s.records_count || kept;
        const mode = s.ingestion_mode || "";
        const tags = [];
        if (s.dropped_topic_mismatch) tags.push(`topic_mismatch: ${s.dropped_topic_mismatch}`);
        if (s.dropped_missing_date) tags.push(`no_date: ${s.dropped_missing_date}`);
        if (s.dropped_old_date) tags.push(`too_old: ${s.dropped_old_date}`);
        if (s.dropped_no_title_url) tags.push(`no_title/url: ${s.dropped_no_title_url}`);
        const tagHtml = tags.length ? `<span style="font-size:12px;color:#6b7280;"> (${tags.join(", ")})</span>` : "";
        return `<div style="padding:6px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;">
          <span><strong>${domain}</strong> <span class="muted">[${mode}]</span>${tagHtml}</span>
          <span><strong>${kept}</strong> / ${total} articles</span>
        </div>`;
      }).join("");
      document.getElementById("source-breakdown").innerHTML = html;
    }

    function renderPublishedDrafts(drafts) {
      const el = document.getElementById("published-drafts");
      if (!drafts || !drafts.length) {
        el.innerHTML = "<span class='muted'>None published yet.</span>";
        return;
      }
      const html = drafts.map((d, idx) => {
        const at = (d.published_at || "").replace(/</g, "&lt;");
        const draftText = (d.draft || "").replace(/</g, "&lt;");
        const aid = (d.article_id || "-").replace(/</g, "&lt;");
        const preview = draftText.length > 200 ? draftText.slice(0, 200) + "..." : draftText;
        return `
          <div class="history-item" style="cursor:pointer;" onclick="loadDraft(${idx})">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <div><strong>Article ID: ${aid}</strong> &middot; Published at: ${at}</div>
              <button class="alt" style="width:auto; padding:4px 10px; font-size:12px; background:#dc2626;" onclick="event.stopPropagation(); deleteDraft(${idx})">Delete</button>
            </div>
            <div class="muted" style="margin-top:6px; white-space:pre-wrap;">${preview}</div>
          </div>
        `;
      }).join("");
      el.innerHTML = html;
    }

    function renderDraftVersions(versions) {
      const el = document.getElementById("draft-versions");
      if (!versions || !versions.length) {
        el.innerHTML = "<span class='muted'>No versions yet.</span>";
        return;
      }
      const html = versions.slice().reverse().map((v, idxFromTop) => {
        const idx = versions.length - 1 - idxFromTop;
        const source = (v.source || "").replace(/</g, "&lt;");
        const at = (v.created_at || "").replace(/</g, "&lt;");
        const draftText = (v.draft || "").replace(/</g, "&lt;");
        const preview = draftText.length > 160 ? draftText.slice(0, 160) + "..." : draftText;
        return `
          <div class="history-item" style="cursor:pointer;" onclick="loadDraftVersion(${idx})">
            <div><strong>${source}</strong> <span class="muted">&middot; ${at}</span></div>
            <div class="muted" style="margin-top:4px; white-space:pre-wrap;">${preview}</div>
          </div>
        `;
      }).join("");
      el.innerHTML = html;
    }

    let CURRENT_DRAFT_VERSIONS = [];

    function loadDraftVersion(idx) {
      const version = CURRENT_DRAFT_VERSIONS[idx];
      if (!version) return;
      draftEditorEl.value = version.draft;
      renderHashtagChips();
      toast(`Loaded version: ${version.source}`, "info");
    }
    window.loadDraftVersion = loadDraftVersion;

    function renderHashtagChips() {
      const container = document.getElementById("hashtag-chips");
      const current = draftEditorEl.value || "";
      container.innerHTML = HASHTAG_LIBRARY.map((tag) => {
        const active = current.includes(tag);
        const style = active
          ? "flex:0 0 auto; padding:4px 10px; font-size:12px; background:#0e7490;"
          : "flex:0 0 auto; padding:4px 10px; font-size:12px; background:#94a3b8;";
        return `<button class="alt" style="${style}" onclick="toggleHashtag('${tag}')">${tag}</button>`;
      }).join("");
    }

    function toggleHashtag(tag) {
      const current = draftEditorEl.value || "";
      if (current.includes(tag)) {
        draftEditorEl.value = current.split(tag).join("").replace(/[ \\t]+\\n/g, "\\n").replace(/\\s+$/, "");
      } else {
        const sep = current && !current.endsWith("\\n") ? "\\n" : "";
        draftEditorEl.value = current + sep + tag;
      }
      renderHashtagChips();
    }
    window.toggleHashtag = toggleHashtag;

    function downloadDraft(filename, mimeType) {
      const text = draftEditorEl.value || "";
      if (!text || text === "No draft yet.") {
        toast("No draft available to download yet.", "error");
        return;
      }
      const blob = new Blob([text], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }

    document.getElementById("download-txt-btn").addEventListener("click", () => downloadDraft("draft.txt", "text/plain"));
    document.getElementById("download-md-btn").addEventListener("click", () => downloadDraft("draft.md", "text/markdown"));

    async function loadDraft(idx) {
      const threadId = threadIdEl.value.trim();
      if (!threadId) return;
      setActivity(`Loading draft ${idx}...`);
      const res = await fetch(`/api/published-draft/load/${encodeURIComponent(threadId)}/${idx}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        toast(data.detail || "Failed to load draft", "error");
        return;
      }
      applyState(data.state, data.history || []);
    }
    window.loadDraft = loadDraft;

    async function deleteDraft(idx) {
      if (!confirm("Delete this published draft?")) return;
      const threadId = threadIdEl.value.trim();
      if (!threadId) return;
      const res = await fetch(`/api/published-draft/delete/${encodeURIComponent(threadId)}/${idx}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        toast(data.detail || "Failed to delete draft", "error");
        return;
      }
      applyState(data.state, data.history || []);
    }
    window.deleteDraft = deleteDraft;

    async function copyDraftMarkdown() {
      const text = draftEditorEl.value || "";
      if (!text || text === "No draft yet.") {
        toast("No draft available to copy yet.", "error");
        return;
      }
      await navigator.clipboard.writeText(text);
      toast("Draft copied to clipboard.", "success");
    }

    async function openLinkedIn() {
      const text = draftEditorEl.value || "";
      if (!text || text === "No draft yet.") {
        toast("No draft to post. Generate a draft first.", "error");
        return;
      }
      const url = "https://www.linkedin.com/post/new/?content=" + encodeURIComponent(text);
      window.open(url, "_blank");
    }

    async function approveDraft() {
      const payload = {
        thread_id: threadIdEl.value.trim(),
        action: "publish",
      };
      const res = await fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to approve draft");
      applyState(data.state, data.history || []);
    }

    async function saveEditDraft() {
      const edited = draftEditorEl.value || "";
      if (!edited.trim()) {
        toast("Draft is empty. Write something before saving.", "error");
        return;
      }
      const payload = {
        thread_id: threadIdEl.value.trim(),
        action: "edit",
        edited_draft: edited,
      };
      const res = await fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to save draft edit");
      applyState(data.state, data.history || []);
    }

    async function pickAnotherArticle() {
      const payload = {
        thread_id: threadIdEl.value.trim(),
        action: "pick_another",
      };
      const res = await fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to pick another article");
      applyState(data.state, data.history || []);
    }

    async function resetThread() {
      if (!confirm("Reset this thread? All state and history will be lost.")) return;
      const threadId = threadIdEl.value.trim();
      if (!threadId) return;
      const res = await fetch(`/api/reset/${encodeURIComponent(threadId)}`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to reset thread");
      resetSteps();
      setFlowStep("scout");
      setActivity("Thread reset. Start a new run.");
      stateJson.textContent = "{}";
      scoutDebugEl.textContent = "No scout debug yet.";
      candidates.innerHTML = "<span class='muted'>No candidates yet.</span>";
      rawArticlesEl.innerHTML = "<span class='muted'>No raw articles yet.</span>";
      selectedIdEl.value = "";
      draftEditorEl.value = "";
      healthResultsEl.textContent = "Provider health checks not run yet.";
      droppedAuditEl.innerHTML = "<span class='muted'>No dropped articles yet.</span>";
      domainDiagnosticsEl.innerHTML = "<span class='muted'>No domain diagnostics yet.</span>";
      historyEl.innerHTML = "<span class='muted'>No history yet.</span>";
      document.getElementById("published-drafts").innerHTML = "<span class='muted'>None published yet.</span>";
      CURRENT_DRAFT_VERSIONS = [];
      renderDraftVersions([]);
      renderHashtagChips();
      toast(data.message || "Thread reset.", "success");
    }

    async function startFlow() {
      startStartProgressAnimation();
      const payload = {
        thread_id: threadIdEl.value.trim(),
        topic: topicEl.value.trim(),
        include_domains: getSelectedDomains(),
        analyst_provider: analystProviderEl.value,
        writer_provider: writerProviderEl.value,
        analyst_model: analystModelEl.value.trim() || null,
        writer_model: writerModelEl.value.trim() || null,
        persona: personaSelectEl.value,
        format: formatSelectEl.value,
      };
      const res = await fetch("/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to start flow");
      }
      applyState(data.state, data.history || []);
    }

    async function testProviders() {
      healthResultsEl.textContent = "Running provider checks...";
      setActivity("Testing provider connectivity...");
      const payload = {
        analyst_provider: analystProviderEl.value,
        writer_provider: writerProviderEl.value,
        analyst_model: analystModelEl.value.trim() || null,
        writer_model: writerModelEl.value.trim() || null,
      };
      const res = await fetch("/api/provider-health", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Provider health check failed");
      }
      healthResultsEl.textContent = JSON.stringify(data, null, 2);
    }

    async function resumeFlow() {
      stepAuthorEl.classList.remove("done");
      stepAuthorEl.classList.add("active");
      stepEditApprovalEl.classList.remove("done", "active");
      setFlowStep("author");
      setActivity("Generating draft...");
      progressSectionEl.style.display = "block";
      progressFillEl.style.width = "95%";
      progressPctEl.textContent = "95%";
      draftEditorEl.value = "";
      const tid = threadIdEl.value.trim();
      const payload = {
        thread_id: tid,
        selected_article_id: selectedIdEl.value.trim(),
        human_feedback: feedbackEl.value.trim() || null,
      };
      const streamPromise = startDraftStream(tid);
      const res = await fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        progressSectionEl.style.display = "none";
        throw new Error(data.detail || "Failed to resume flow");
      }
      applyState(data.state, data.history || []);
    }

    async function startDraftStream(threadId) {
      const evtSource = new EventSource(`/api/stream/${encodeURIComponent(threadId)}`);
      evtSource.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.done) {
            evtSource.close();
            return;
          }
          if (msg.tokens) {
            draftEditorEl.value += msg.tokens.join("");
            draftEditorEl.scrollTop = draftEditorEl.scrollHeight;
          }
        } catch (_) {}
      };
      evtSource.onerror = () => { evtSource.close(); };
    }

    async function withUiErrors(fn) {
      try {
        await fn();
      } catch (err) {
        stopStartProgressAnimation();
        toast(err.message || String(err), "error", 6000);
        setActivity(`Action failed: ${err.message || String(err)}`);
      }
    }

    document.getElementById("start-btn").addEventListener("click", () => withBusyButton(document.getElementById("start-btn"), "Starting...", () => withUiErrors(startFlow)));
    document.getElementById("test-providers-btn").addEventListener("click", () => withBusyButton(document.getElementById("test-providers-btn"), "Testing...", () => withUiErrors(testProviders)));
    document.getElementById("refresh-btn").addEventListener("click", () => withBusyButton(document.getElementById("refresh-btn"), "Refreshing...", () => withUiErrors(fetchState)));
    document.getElementById("resume-btn").addEventListener("click", () => withBusyButton(document.getElementById("resume-btn"), "Generating...", () => withUiErrors(resumeFlow)));
    document.getElementById("copy-draft-btn").addEventListener("click", () => withUiErrors(copyDraftMarkdown));
    document.getElementById("open-linkedin-btn").addEventListener("click", () => withUiErrors(openLinkedIn));
    document.getElementById("approve-draft-btn").addEventListener("click", () => withBusyButton(document.getElementById("approve-draft-btn"), "Publishing...", () => withUiErrors(approveDraft)));
    document.getElementById("edit-draft-btn").addEventListener("click", () => withBusyButton(document.getElementById("edit-draft-btn"), "Saving...", () => withUiErrors(saveEditDraft)));
    document.getElementById("pick-another-btn").addEventListener("click", () => withBusyButton(document.getElementById("pick-another-btn"), "Switching...", () => withUiErrors(pickAnotherArticle)));
    document.getElementById("reset-btn").addEventListener("click", () => withUiErrors(resetThread));
    document.getElementById("add-domain-btn").addEventListener("click", addDomain);

    async function refineDraft(instruction) {
      const current = draftEditorEl.value || "";
      if (!current || current === "No draft yet.") {
        toast("No draft to refine. Generate a draft first.", "error");
        return;
      }
      const tid = threadIdEl.value.trim();
      setActivity(`Refining: ${instruction}...`);
      const res = await fetch("/api/refine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: tid, instruction, current_draft: current }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Refinement failed");
      draftEditorEl.value = data.refined_draft;
      setActivity("Draft refined.");
      offerPromoteRefine(instruction);
    }

    function offerPromoteRefine(instruction) {
      // One-click promote: offer to save the refine instruction as a style rule.
      // Auto-dismisses after 6s. Not auto-saved — user must click.
      const container = document.getElementById("toast-container");
      const el = document.createElement("div");
      el.className = "toast toast-info";
      el.style.cursor = "default";
      el.innerHTML = `<span>Save this as a style rule?</span>
        <button class="alt" style="width:auto; padding:3px 10px; font-size:12px; margin-left:10px;">Save</button>`;
      const btn = el.querySelector("button");
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const persona = personaSelectEl.value || "*";
        const r = await fetch("/api/style-rules", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rule_text: instruction, persona, source: "feedback" }),
        });
        const d = await r.json();
        if (!r.ok) { toast(d.detail || "Failed to save rule", "error"); return; }
        toast("Saved as style rule.", "success");
        el.remove();
        loadStyleRules();
      });
      el.addEventListener("click", () => el.remove());
      container.appendChild(el);
      requestAnimationFrame(() => el.classList.add("toast-visible"));
      setTimeout(() => {
        el.classList.remove("toast-visible");
        setTimeout(() => el.remove(), 200);
      }, 6000);
    }

    document.getElementById("refine-hook").addEventListener("click", () => withBusyButton(document.getElementById("refine-hook"), "...", () => withUiErrors(() => refineDraft("Make the hook punchier and more contrarian. Start with a blunt, provocative statement."))));
    document.getElementById("refine-shorten").addEventListener("click", () => withBusyButton(document.getElementById("refine-shorten"), "...", () => withUiErrors(() => refineDraft("Shorten this draft. Cut filler words, tighten sentences, and get closer to 150 words while keeping the core message."))));
    document.getElementById("refine-technical").addEventListener("click", () => withBusyButton(document.getElementById("refine-technical"), "...", () => withUiErrors(() => refineDraft("Make this more technical. Add specific implementation details, architecture tradeoffs, or engineering considerations. Use precise technical terminology."))));
    document.getElementById("refine-cta").addEventListener("click", () => withBusyButton(document.getElementById("refine-cta"), "...", () => withUiErrors(() => refineDraft("Strengthen the closing. End with a provocative, opinionated question that invites debate from technical peers."))));
    document.getElementById("refine-grammar").addEventListener("click", () => withBusyButton(document.getElementById("refine-grammar"), "...", () => withUiErrors(() => refineDraft("Fix grammar, spelling, and punctuation. Improve sentence flow and readability without changing the content or tone."))));

    async function loadScheduledRuns() {
      const res = await fetch("/api/scheduled-runs");
      const data = await res.json();
      if (!data.ok || !data.runs || !data.runs.length) {
        document.getElementById("scheduled-runs").innerHTML = "<span class='muted'>No scheduled runs yet.</span>";
        return;
      }
      const html = data.runs.map(r => {
        const runId = (r.run_id || "").replace(/</g, "&lt;");
        const topic = (r.topic || "").replace(/</g, "&lt;");
        const triggered = (r.triggered_at || "").replace(/</g, "&lt;");
        const emailed = r.email_sent_at ? "yes" : "no";
        const reviewed = r.reviewed_at ? "yes" : "no";
        const reviewBtn = r.reviewed_at
          ? ""
          : `<button class="alt" style="width:auto; padding:4px 10px; font-size:12px;" onclick="markRunReviewed('${runId}', event)">Mark Reviewed</button>`;
        return `<div class="history-item" style="cursor:pointer;" onclick="loadScheduledRun('${runId}')">
          <div><strong>${runId}</strong></div>
          <div class="muted">Topic: ${topic}</div>
          <div class="history-meta" style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
            <span>Triggered: ${triggered} | Emailed: ${emailed} | Reviewed: ${reviewed}</span>
            ${reviewBtn}
          </div>
        </div>`;
      }).join("");
      document.getElementById("scheduled-runs").innerHTML = html;
    }

    async function markRunReviewed(runId, event) {
      if (event) event.stopPropagation();
      const res = await fetch(`/api/scheduled-runs/${encodeURIComponent(runId)}/review`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        toast(data.detail || "Failed to mark run reviewed", "error");
        return;
      }
      loadScheduledRuns();
    }
    window.markRunReviewed = markRunReviewed;

    async function loadScheduledRun(runId) {
      setActivity(`Loading scheduled run ${runId}...`);
      const res = await fetch(`/api/scheduled-runs/${encodeURIComponent(runId)}`);
      const data = await res.json();
      if (!res.ok) {
        toast(data.detail || "Failed to load run", "error");
        return;
      }
      threadIdEl.value = data.run.thread_id;
      selectedIdEl.value = "";
      const candidates = data.run.candidates || [];
      renderCandidates(candidates);
      resetSteps();
      setFlowStep("approval");
      setActivity(`Switched to scheduled run ${runId} (thread: ${data.run.thread_id}). Loaded ${candidates.length} candidates. Select one and generate a draft.`);
    }
    window.loadScheduledRun = loadScheduledRun;

    // ---------- Style Rules ----------
    async function loadStyleRules() {
      try {
        const res = await fetch("/api/style-rules");
        const data = await res.json();
        if (!res.ok) return;
        renderStyleRules(data.rules || []);
      } catch (_) {}
    }

    function renderStyleRules(rules) {
      const el = document.getElementById("style-rules-list");
      if (!rules || !rules.length) {
        el.innerHTML = "<span class='muted'>No style rules yet.</span>";
        return;
      }
      const esc = (s) => String(s || "").replace(/</g, "&lt;");
      const html = rules.map((r) => {
        const text = esc(r.rule_text);
        const persona = esc(r.persona);
        const src = esc(r.source);
        const applied = r.applied_count || 0;
        const dis = r.disabled ? " (disabled)" : "";
        const toggleLabel = r.disabled ? "Enable" : "Disable";
        return `
          <div class="history-item" style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px; ${r.disabled ? 'opacity:0.55;' : ''}">
            <div style="min-width:0; overflow-wrap:anywhere;">
              <div><strong>${text}</strong> <span class="muted">&middot; persona: ${persona} &middot; src: ${src} &middot; applied: ${applied}${dis}</span></div>
            </div>
            <div style="flex:0 0 auto; display:flex; gap:4px;">
              <button class="alt" style="width:auto; padding:3px 8px; font-size:11px;" onclick="toggleStyleRule(${r.id})">${toggleLabel}</button>
              <button class="alt" style="width:auto; padding:3px 8px; font-size:11px; background:#dc2626;" onclick="deleteStyleRule(${r.id})">Delete</button>
            </div>
          </div>`;
      }).join("");
      el.innerHTML = html;
    }

    async function addStyleRule() {
      const input = document.getElementById("new-style-rule");
      const personaSel = document.getElementById("new-rule-persona");
      const text = input.value.trim();
      if (!text) { toast("Rule text cannot be empty.", "error"); return; }
      const res = await fetch("/api/style-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rule_text: text, persona: personaSel.value, source: "manual" }),
      });
      const data = await res.json();
      if (!res.ok) { toast(data.detail || "Failed to add rule", "error"); return; }
      input.value = "";
      toast("Style rule added.", "success");
      loadStyleRules();
    }

    async function toggleStyleRule(ruleId) {
      const res = await fetch(`/api/style-rules/${ruleId}/toggle`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) { toast(data.detail || "Failed to toggle rule", "error"); return; }
      loadStyleRules();
    }

    async function deleteStyleRule(ruleId) {
      const res = await fetch(`/api/style-rules/${ruleId}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) { toast(data.detail || "Failed to delete rule", "error"); return; }
      toast("Style rule deleted.", "info");
      loadStyleRules();
    }

    window.toggleStyleRule = toggleStyleRule;
    window.deleteStyleRule = deleteStyleRule;

    document.getElementById("add-rule-btn").addEventListener("click", withUiErrors(addStyleRule));
    document.getElementById("new-style-rule").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); withUiErrors(addStyleRule)(); }
    });

    loadScheduledRuns();
    loadStyleRules();

    const deepLinkRunId = new URLSearchParams(window.location.search).get("run");
    if (deepLinkRunId) {
      loadScheduledRun(deepLinkRunId);
    }

    function updateModelList(providerId, inputId) {
      const provider = document.getElementById(providerId).value;
      const input = document.getElementById(inputId);
      input.setAttribute("list", provider + "-models");
    }
    analystProviderEl.addEventListener("change", () => updateModelList("analyst-provider", "analyst-model"));
    writerProviderEl.addEventListener("change", () => updateModelList("writer-provider", "writer-model"));

    newDomainEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addDomain();
      }
    });
    resetSteps();
    setFlowStep("scout");
    setActivity("Ready.");
    renderDomainList();
    hydrateDomainsFromServer();
    updateModelList("analyst-provider", "analyst-model");
    updateModelList("writer-provider", "writer-model");
    // ---------- Performance Dashboard ----------
    async function loadDashboard() {
      const el = document.getElementById("dashboard-content");
      const days = parseInt(document.getElementById("dashboard-days").value, 10) || 30;
      el.innerHTML = "<span class='muted'>Loading...</span>";
      try {
        const res = await fetch(`/api/dashboard?days=${days}`);
        const data = await res.json();
        if (!res.ok) { el.innerHTML = "<span class='muted'>Failed to load dashboard.</span>"; return; }
        renderDashboard(data, days);
      } catch (err) {
        el.innerHTML = "<span class='muted'>Failed to load dashboard.</span>";
      }
    }

    function renderDashboard(data, days) {
      const el = document.getElementById("dashboard-content");
      const esc = (s) => String(s || "").replace(/</g, "&lt;");
      const fmtCost = (v) => v ? `$${Number(v).toFixed(4)}` : "$0.00";
      const cost = data.cost || {};
      const runs = data.runs || {};
      const drafts = data.drafts || {};
      const topics = data.topics || {};
      const style = data.style_rules || {};

      let html = "<h3>Cost (last " + days + " days)</h3>";
      html += `<div class="muted" style="margin-bottom:6px;">Total: ${fmtCost(cost.total_cost_usd)} across ${cost.call_count || 0} LLM calls</div>`;
      const nodeRows = Object.entries(cost.by_node || {}).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td style="text-align:right;">${fmtCost(v)}</td></tr>`).join("");
      const provRows = Object.entries(cost.by_provider || {}).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td style="text-align:right;">${fmtCost(v)}</td></tr>`).join("");
      html += `<div class="row" style="gap:16px; flex-wrap:wrap;"><div><table style="border-collapse:collapse; font-size:13px;"><thead><tr><th style="text-align:left;padding:2px 12px 2px 0;">Node</th><th style="text-align:right;padding:2px 0;">Cost</th></tr></thead><tbody>${nodeRows}</tbody></table></div><div><table style="border-collapse:collapse; font-size:13px;"><thead><tr><th style="text-align:left;padding:2px 12px 2px 0;">Provider</th><th style="text-align:right;padding:2px 0;">Cost</th></tr></thead><tbody>${provRows}</tbody></table></div></div>`;

      html += "<h3 style='margin-top:14px;'>Runs</h3>";
      if (runs.total_runs === 0) {
        html += "<div class='muted'>No runs in this period.</div>";
      } else {
        html += `<div class="muted" style="margin-bottom:6px;">${runs.total_runs} runs | ${runs.total_emailed} emailed (${runs.email_rate}%) | ${runs.total_reviewed} reviewed (${runs.review_rate}%) | avg ${runs.avg_candidates_per_run} candidates/run</div>`;
        const weekRows = (runs.weekly || []).map(w =>
          `<tr><td style="padding:2px 12px 2px 0;">${esc(w.week)}</td><td style="text-align:right;padding:2px 8px;">${w.runs}</td><td style="text-align:right;padding:2px 8px;">${w.avg_candidates}</td><td style="text-align:right;padding:2px 0;">${w.review_rate}%</td></tr>`).join("");
        html += `<table style="border-collapse:collapse; font-size:13px;"><thead><tr><th style="text-align:left;padding:2px 12px 2px 0;">Week</th><th style="text-align:right;padding:2px 8px;">Runs</th><th style="text-align:right;padding:2px 8px;">Avg Cands</th><th style="text-align:right;padding:2px 0;">Reviewed</th></tr></thead><tbody>${weekRows}</tbody></table>`;
      }

      html += "<h3 style='margin-top:14px;'>Drafts</h3>";
      if (drafts.total_published === 0) {
        html += "<div class='muted'>No published drafts in this period.</div>";
      } else {
        html += `<div class="muted">${drafts.total_published} published | avg ${drafts.avg_draft_length} chars | ${drafts.refine_count} refines | ${drafts.author_count} author drafts | ${drafts.manual_edit_count} manual edits</div>`;
      }

      html += "<h3 style='margin-top:14px;'>Topic Distribution</h3>";
      const topicItems = Object.entries(topics.topics || {}).map(([k, v]) =>
        `<span class="pill">${esc(k)}: ${v}</span>`).join(" ");
      html += `<div class="row" style="gap:6px; flex-wrap:wrap;">${topicItems || "<span class='muted'>No topics.</span>"}</div>`;

      html += "<h3 style='margin-top:14px;'>Style Rules</h3>";
      if (style.total_rules === 0) {
        html += "<div class='muted'>No style rules defined.</div>";
      } else {
        html += `<div class="muted" style="margin-bottom:6px;">${style.active_rules} active, ${style.disabled_rules} disabled</div>`;
        const ruleRows = (style.rules || []).map(r =>
          `<tr style="${r.disabled ? 'opacity:0.55;' : ''}"><td style="padding:2px 12px 2px 0;">${esc(r.rule_text)}</td><td style="padding:2px 8px;">${esc(r.persona)}</td><td style="text-align:right;padding:2px 0;">${r.applied_count}</td>${r.disabled ? '<td style="padding:2px 0;font-size:11px;">disabled</td>' : ''}</tr>`).join("");
        html += `<table style="border-collapse:collapse; font-size:13px;"><thead><tr><th style="text-align:left;padding:2px 12px 2px 0;">Rule</th><th style="text-align:left;padding:2px 8px;">Persona</th><th style="text-align:right;padding:2px 0;">Applied</th></tr></thead><tbody>${ruleRows}</tbody></table>`;
      }

      el.innerHTML = html;
    }

    document.getElementById("refresh-dashboard-btn").addEventListener("click", () => withUiErrors(loadDashboard));
    document.getElementById("dashboard-days").addEventListener("change", () => withUiErrors(loadDashboard));

    renderHashtagChips();
  </script>
</body>
</html>
"""
    options_html = "\n".join([f'        <option value="{m}">' for m in get_ollama_model_options()])
    return html.replace("__DEFAULT_TOPIC__", escape(get_default_topic())).replace("__OLLAMA_OPTIONS__", options_html)


@web_app.post("/api/start")
def start_flow(payload: StartRequest) -> Dict[str, Any]:
    config = _config(payload.thread_id)
    progress_tracker.clear(payload.thread_id)
    try:
        result = graph_app.invoke(
            {
                "topic": payload.topic,
                "include_domains": payload.include_domains,
                "analyst_provider": payload.analyst_provider,
                "writer_provider": payload.writer_provider,
                "analyst_model": payload.analyst_model,
                "writer_model": payload.writer_model,
                "persona": payload.persona,
                "format": payload.format,
                "thread_id": payload.thread_id,
            },
            config=config,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    state = _state_snapshot(payload.thread_id)
    _record_history(
        payload.thread_id,
        action="start",
        state=state,
        payload={
            "topic": payload.topic,
            "include_domains": payload.include_domains,
            "analyst_provider": payload.analyst_provider,
            "writer_provider": payload.writer_provider,
            "analyst_model": payload.analyst_model,
            "writer_model": payload.writer_model,
        },
    )

    return {
        "ok": True,
        "interrupted": bool(result.get("__interrupt__")),
        "state": state,
        "history": _history_for(payload.thread_id),
    }


@web_app.post("/api/provider-health")
def provider_health(payload: ProviderHealthRequest) -> Dict[str, Any]:
    checks = [
        (payload.analyst_provider, payload.analyst_model),
        (payload.writer_provider, payload.writer_model),
    ]
    deduped: List[tuple[str, Optional[str]]] = []
    for provider, model in checks:
        item = ((provider or "").strip().lower(), (model or "").strip() or None)
        if item not in deduped:
            deduped.append(item)

    results = [_check_provider_health(provider, model) for provider, model in deduped]
    return {
        "ok": all(item.get("ok") for item in results),
        "results": results,
    }


@web_app.get("/api/progress/{thread_id}")
def get_progress(thread_id: str) -> Dict[str, Any]:
    data = progress_tracker.get_all(thread_id)
    return {
        "ok": True,
        "thread_id": thread_id,
        "phase": data.get("phase", ""),
        "pct": data.get("pct", 0),
        "message": data.get("message", ""),
        "current_source": data.get("current_source", ""),
        "completed_sources": data.get("completed_sources", 0),
        "total_sources": data.get("total_sources", 0),
        "source_log": list(data.get("source_log") or []),
        "source_results": list(data.get("source_results") or []),
    }


@web_app.post("/api/resume")
def resume_flow(payload: ResumeRequest) -> Dict[str, Any]:
    if payload.action:
        resume_payload: Dict[str, Any] = {"action": payload.action}
        if payload.edited_draft:
            resume_payload["edited_draft"] = payload.edited_draft
        if payload.human_feedback:
            resume_payload["human_feedback"] = payload.human_feedback
    else:
        resume_payload: Dict[str, Any] = {"selected_article_id": payload.selected_article_id}
        if payload.human_feedback:
            resume_payload["human_feedback"] = payload.human_feedback

    config = _config(payload.thread_id)
    try:
        if payload.action:
            graph_app.invoke(Command(resume=resume_payload), config=config)
        else:
            current_state = graph_app.get_state(config)
            next_nodes = list(current_state.next) if current_state.next else []
            if "edit_approval" in next_nodes:
                graph_app.invoke(Command(resume={"action": "pick_another"}), config=config)
            graph_app.invoke(Command(resume=resume_payload), config=config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload.action == "edit" and payload.edited_draft:
        draft_store.add_version(payload.thread_id, payload.edited_draft, "manual_edit")

    state = _state_snapshot(payload.thread_id)

    # Sync any newly published drafts from graph state to DB store
    s = graph_app.get_state(config)
    values = s.values if isinstance(s.values, dict) else {}
    new_published = values.get("published_drafts", [])
    existing = draft_store.get_drafts(payload.thread_id)
    for nd in new_published:
        already = any(
            e.get("draft") == nd.get("draft") and e.get("article_id") == nd.get("article_id")
            for e in existing
        )
        if not already:
            draft_store.add_draft(
                payload.thread_id,
                str(nd.get("article_id", "")),
                nd.get("draft", ""),
                nd.get("published_at", datetime.now(timezone.utc).isoformat()),
            )

    _record_history(
        payload.thread_id,
        action="resume",
        state=state,
        payload=resume_payload,
    )

    return {
        "ok": True,
        "state": _state_snapshot(payload.thread_id),
        "history": _history_for(payload.thread_id),
    }


@web_app.get("/api/state/{thread_id}")
def get_state(thread_id: str) -> Dict[str, Any]:
    try:
        snapshot = _state_snapshot(thread_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"No state found for thread '{thread_id}'") from exc
    _record_history(thread_id, action="refresh", state=snapshot)
    return {"ok": True, "state": snapshot, "history": _history_for(thread_id)}


@web_app.post("/api/published-draft/load/{thread_id}/{draft_index}")
def load_published_draft(thread_id: str, draft_index: int) -> Dict[str, Any]:
    drafts = draft_store.get_drafts(thread_id)
    if draft_index < 0 or draft_index >= len(drafts):
        raise HTTPException(status_code=404, detail=f"Draft index {draft_index} out of range")

    draft_to_load = drafts[draft_index]
    try:
        graph_app.update_state(
            {"configurable": {"thread_id": thread_id}},
            {
                "final_draft": draft_to_load.get("draft", ""),
                "workflow_status": "awaiting_draft_approval",
                "selected_article_id": draft_to_load.get("article_id", ""),
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    snapshot = _state_snapshot(thread_id)
    return {"ok": True, "state": snapshot, "history": _history_for(thread_id)}


@web_app.post("/api/published-draft/delete/{thread_id}/{draft_index}")
def delete_published_draft(thread_id: str, draft_index: int) -> Dict[str, Any]:
    drafts = draft_store.get_drafts(thread_id)
    if draft_index < 0 or draft_index >= len(drafts):
        raise HTTPException(status_code=404, detail=f"Draft index {draft_index} out of range")

    draft_store.delete_draft(thread_id, draft_index)

    snapshot = _state_snapshot(thread_id)
    return {"ok": True, "state": snapshot, "history": _history_for(thread_id)}


@web_app.post("/api/reset/{thread_id}")
def reset_thread(thread_id: str) -> Dict[str, Any]:
    global RUN_HISTORY
    try:
        graph_app.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        raise HTTPException(status_code=404, detail=f"No state found for thread '{thread_id}'") from None

    import langgraph.errors
    try:
        graph_app.update_state({"configurable": {"thread_id": thread_id}}, {"workflow_status": "reset"})
    except Exception:
        pass
    RUN_HISTORY.pop(thread_id, None)
    progress_tracker.clear(thread_id)
    draft_store.delete_all_for_thread(thread_id)
    return {"ok": True, "message": f"Thread '{thread_id}' reset. Start a new run to begin fresh."}


@web_app.get("/favicon.ico")
def favicon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#0e7490"/>
  <text x="16" y="23" font-family="sans-serif" font-size="20" font-weight="bold" fill="white" text-anchor="middle">AP</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@web_app.get("/api/stream/{thread_id}")
async def stream_draft(thread_id: str):
    async def event_generator():
        last_idx = 0
        max_iterations = int(SSE_STREAM_TIMEOUT_SECONDS / SSE_POLL_INTERVAL_SECONDS)
        for _ in range(max_iterations):
            tokens = progress_tracker.get_stream_tokens(thread_id)
            new_tokens = tokens[last_idx:]
            last_idx = len(tokens)
            if new_tokens:
                yield f"data: {_json.dumps({'tokens': new_tokens})}\n\n"
            if progress_tracker.is_stream_done(thread_id):
                yield f"data: {_json.dumps({'done': True})}\n\n"
                return
            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
        yield f"data: {_json.dumps({'done': True})}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@web_app.post("/api/refine")
def refine_draft(payload: RefineRequest) -> Dict[str, Any]:
    config = _config(payload.thread_id)
    state = graph_app.get_state(config)
    values = state.values if isinstance(state.values, dict) else {}
    writer_provider = values.get("writer_provider", "ollama")
    writer_model = values.get("writer_model")
    writer_llm = _get_chat_model(writer_provider, role="refine", model_override=writer_model)
    persona = values.get("persona") or DEFAULT_PERSONA
    persona_config = PERSONAS.get(persona) or PERSONAS[DEFAULT_PERSONA]
    fmt = values.get("format") or DEFAULT_FORMAT
    fconfig = FORMATS.get(fmt) or FORMATS[DEFAULT_FORMAT]
    rules_block = style_profile.active_rules_block(persona)

    format_hint = f" The draft is in the following format: {fconfig['intro']}" if fmt != "post" else ""
    prompt = f"""You are refining a LinkedIn draft. {persona_config['intro']}{format_hint} Apply the following instruction to the draft below, keeping the voice consistent with that persona. Return ONLY the revised draft text — no explanations, no markdown, no commentary.
{f'{chr(10)}{rules_block}{chr(10)}' if rules_block else ''}
Instruction: {payload.instruction}

Current draft:
{payload.current_draft}"""

    response = writer_llm.invoke(prompt)
    refined = str(response.content)
    input_tokens, output_tokens, estimated = _capture_usage(response, prompt, refined)
    cost_tracker.log_usage(
        payload.thread_id, "refine", writer_provider, _resolve_model_name(writer_llm, writer_model),
        input_tokens, output_tokens, estimated,
    )
    draft_store.add_version(payload.thread_id, refined, f"refine: {payload.instruction}")
    active_rules = style_profile.get_active_rules(persona)
    if active_rules:
        style_profile.increment_applied([r["id"] for r in active_rules])
    return {"ok": True, "refined_draft": refined}


@web_app.get("/api/style-rules")
def list_style_rules(include_disabled: bool = False) -> Dict[str, Any]:
    return {"ok": True, "rules": style_profile.list_rules(include_disabled=include_disabled)}


@web_app.post("/api/style-rules")
def create_style_rule(payload: StyleRuleRequest) -> Dict[str, Any]:
    try:
        rule = style_profile.add_rule(payload.rule_text, persona=payload.persona, source=payload.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "rule": rule}


@web_app.patch("/api/style-rules/{rule_id}")
def update_style_rule(rule_id: int, payload: StyleRuleUpdateRequest) -> Dict[str, Any]:
    try:
        rule = style_profile.update_rule(rule_id, rule_text=payload.rule_text, persona=payload.persona)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"ok": True, "rule": rule}


@web_app.post("/api/style-rules/{rule_id}/toggle")
def toggle_style_rule(rule_id: int) -> Dict[str, Any]:
    rule = style_profile.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    style_profile.set_disabled(rule_id, not rule["disabled"])
    return {"ok": True, "rule": style_profile.get_rule(rule_id)}


@web_app.delete("/api/style-rules/{rule_id}")
def delete_style_rule(rule_id: int) -> Dict[str, Any]:
    if not style_profile.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"ok": True}


@web_app.get("/api/scheduled-runs")
def list_scheduled_runs() -> Dict[str, Any]:
    runs = scheduled_store.list_runs()
    return {"ok": True, "runs": runs}


@web_app.get("/api/scheduled-runs/{run_id}")
def get_scheduled_run(run_id: str) -> Dict[str, Any]:
    run = scheduled_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"ok": True, "run": run}


@web_app.post("/api/scheduled-runs/{run_id}/review")
def mark_scheduled_run_reviewed(run_id: str) -> Dict[str, Any]:
    scheduled_store.mark_reviewed(run_id)
    return {"ok": True}


@web_app.get("/api/scheduled-runs/{run_id}/skip", response_class=HTMLResponse)
def skip_scheduled_run(run_id: str) -> str:
    # GET (not POST) so this can be a plain clickable link from the digest email.
    scheduled_store.mark_reviewed(run_id)
    return (
        f"<p style=\"font-family:sans-serif;\">Run <code>{escape(run_id)}</code> marked as "
        "reviewed/skipped. You can close this tab.</p>"
    )


@web_app.get("/api/scheduler/status")
def scheduler_status() -> Dict[str, Any]:
    return {"ok": True, "running": scheduler.is_running()}


@web_app.post("/api/webhook/trigger")
def webhook_trigger(
    payload: WebhookStartRequest,
    x_webhook_secret: str = Header(default=""),
) -> Dict[str, Any]:
    configured_secret = (os.getenv("WEBHOOK_SECRET") or "").strip()
    if not configured_secret:
        raise HTTPException(
            status_code=501,
            detail="Webhook trigger is disabled. Set WEBHOOK_SECRET to enable /api/webhook/trigger.",
        )
    if not x_webhook_secret or not secrets.compare_digest(x_webhook_secret, configured_secret):
        raise HTTPException(status_code=403, detail="Invalid or missing X-Webhook-Secret header.")

    topic = payload.topic or os.getenv("WEBHOOK_TOPIC") or get_default_topic()
    include_domains = payload.include_domains or (domain_store.get_enabled_domains() or None)
    analyst_provider = payload.analyst_provider or os.getenv("WEBHOOK_ANALYST_PROVIDER", "ollama")
    writer_provider = payload.writer_provider or os.getenv("WEBHOOK_WRITER_PROVIDER", "ollama")
    analyst_model = payload.analyst_model or (os.getenv("WEBHOOK_ANALYST_MODEL") or None)
    writer_model = payload.writer_model or (os.getenv("WEBHOOK_WRITER_MODEL") or None)
    persona = payload.persona or DEFAULT_PERSONA
    fmt = payload.format or "post"

    now = datetime.now(timezone.utc)
    run_id = f"webhook-{now.strftime('%Y-%m-%d-%H%M%S')}"

    try:
        candidates = scheduler.run_scout_analyst_job(
            topic, include_domains, analyst_provider, writer_provider,
            analyst_model, writer_model, persona, run_id, fmt,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "run_id": run_id,
        "thread_id": run_id,
        "candidate_count": len(candidates),
    }


@web_app.get("/api/costs/summary")
def costs_summary(hours: int = 24) -> Dict[str, Any]:
    return {"ok": True, **cost_tracker.get_recent_totals(hours)}


@web_app.get("/api/costs/{thread_id}")
def costs_for_thread(thread_id: str) -> Dict[str, Any]:
    return {"ok": True, **cost_tracker.get_thread_cost(thread_id)}


@web_app.get("/api/dashboard")
def get_dashboard(days: int = 30) -> Dict[str, Any]:
    days = max(1, min(days, 365))
    return {"ok": True, **dashboard.build_dashboard(days=days)}


@web_app.get("/api/domains")
def get_domains() -> Dict[str, Any]:
    rows = domain_store.get_domains()
    return {
        "ok": True,
        "domains": [r["domain"] for r in rows],
        "disabled": [r["domain"] for r in rows if not r["enabled"]],
    }


@web_app.post("/api/domains")
def save_domains(payload: DomainsRequest) -> Dict[str, Any]:
    domain_store.save_domains(payload.domains, payload.disabled)
    return {"ok": True}


@web_app.on_event("startup")
def on_startup() -> None:
    scheduler.start_scheduler()


@web_app.on_event("shutdown")
def on_shutdown() -> None:
    scheduler.stop_scheduler()


@web_app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}
