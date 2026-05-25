import os
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, Field

import progress as progress_tracker
import draft_store
from graph import _get_chat_model, build_graph
from settings import get_default_topic, get_ollama_model_options


load_dotenv()

web_app = FastAPI(title="Article Pipeline UI")
web_app.mount("/static", StaticFiles(directory="static"), name="static")
graph_app = build_graph()
RUN_HISTORY: Dict[str, List[Dict[str, Any]]] = {}


class StartRequest(BaseModel):
    thread_id: str = Field(default="web-demo-thread")
    topic: str = Field(default_factory=get_default_topic)
    analyst_provider: str = Field(default="ollama")
    writer_provider: str = Field(default="ollama")
    analyst_model: Optional[str] = Field(default=None)
    writer_model: Optional[str] = Field(default=None)
    include_domains: Optional[List[str]] = Field(default=None)


class ResumeRequest(BaseModel):
    thread_id: str = Field(...)
    selected_article_id: Optional[str] = Field(default=None)
    human_feedback: Optional[str] = Field(default=None)

    # Draft review flow
    action: Optional[str] = Field(default=None)
    edited_draft: Optional[str] = Field(default=None)


class ProviderHealthRequest(BaseModel):
    analyst_provider: str = Field(...)
    writer_provider: str = Field(...)
    analyst_model: Optional[str] = Field(default=None)
    writer_model: Optional[str] = Field(default=None)


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
        "raw_articles": values.get("raw_articles", []),
        "scout_debug": values.get("scout_debug", {}),
        "curated_candidates": values.get("curated_candidates", []),
        "selected_article_id": values.get("selected_article_id"),
        "human_feedback": values.get("human_feedback"),
        "final_draft": values.get("final_draft"),
        "published_drafts": draft_store.get_drafts(thread_id),
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
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top right, #d8ecf8 0%, var(--bg) 45%);
    }
    .wrap {
      max-width: 1080px;
      margin: 24px auto;
      padding: 0 16px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(19, 33, 68, 0.06);
    }
    h1 { margin: 0 0 10px; font-size: 24px; }
    h2 { margin: 0 0 10px; font-size: 18px; }
    p, label { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
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
      grid-template-columns: repeat(4, minmax(0, 1fr));
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
    .row { display: flex; gap: 10px; }
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
    }
    .raw-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      background: #f8fafc;
    }
    pre {
      white-space: pre-wrap;
      background: #0f172a;
      color: #dbeafe;
      padding: 12px;
      border-radius: 10px;
      overflow: auto;
    }
    .muted { color: var(--muted); font-size: 14px; }
    .history-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      background: #fbfdff;
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
    }
    .domain-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
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
    }
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
  </style>
</head>
<body>
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
        <div><label>Analyst Model (optional override)</label><input id="analyst-model" list="ollama-models" placeholder="e.g. gemini-2.0-flash or llama3.1" /></div>
        <div><label>Writer Model (optional override)</label><input id="writer-model" list="ollama-models" placeholder="e.g. gpt-4o or llama3.1" /></div>
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
        <option value="gemini-2.0-flash">
        <option value="gemini-1.5-pro">
        <option value="gemini-1.5-flash">
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
      <h2>2) Curated Candidates (Approval Step)</h2>
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
        <button id="resume-btn">Approve + Resume</button>
      </div>
    </section>

    <section class="card">
      <details>
        <summary style="cursor:pointer; font-weight:bold; font-size:18px;"><h2 style="display:inline; margin:0;">2b) Raw Articles (All Scout Results)</h2></summary>
        <div id="raw-articles" class="muted" style="margin-top:10px;">No raw articles yet.</div>
      </details>
    </section>

    <section class="card">
      <h2>3) Draft Review & Publish</h2>
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
      </div>
      <p class="muted" style="margin-top:8px;">
        <strong>Approve & Publish</strong> — marks draft as published.<br/>
        <strong>Save Edit</strong> — saves your edits and re-enters draft review.<br/>
        <strong>Pick Another Article</strong> — go back to approval step (articles preserved).<br/>
        <strong>Open LinkedIn Post</strong> — opens LinkedIn composer with draft content.
      </p>
      <h3 style="margin-top:14px;">Published Drafts (This Thread)</h3>
      <div id="published-drafts" class="muted">None published yet.</div>
    </section>

    <section class="card">
      <h2>Live State JSON</h2>
      <pre id="state-json">{}</pre>
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

    const threadIdEl = document.getElementById("thread-id");
    const topicEl = document.getElementById("topic");
    const analystProviderEl = document.getElementById("analyst-provider");
    const writerProviderEl = document.getElementById("writer-provider");
    const analystModelEl = document.getElementById("analyst-model");
    const writerModelEl = document.getElementById("writer-model");
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

    function saveDomainsState(domains, disabledSet) {
      localStorage.setItem(STORAGE_DOMAINS_KEY, JSON.stringify(domains));
      localStorage.setItem(STORAGE_DISABLED_KEY, JSON.stringify([...disabledSet]));
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
      const html = items.map((item) => {
        const safeTitle = (item.title || "Untitled").replace(/</g, "&lt;");
        const safeSummary = (item.summary || "").replace(/</g, "&lt;");
        return `
          <div class="candidate">
            <div><strong>[${item.id}] ${safeTitle}</strong></div>
            <div class="muted">Source: ${item.source || "unknown"} | Relevance: ${item.relevance_score ?? 0}</div>
            <div style="margin:8px 0;"><a href="${item.url}" target="_blank">${item.url}</a></div>
            <div class="muted">${safeSummary}</div>
            <div style="margin-top:8px;"><button onclick="pickId('${item.id}')">Use ID ${item.id}</button></div>
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
    }
    window.pickId = pickId;


    function setActivity(text) {
      activityTextEl.textContent = text || "Ready.";
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
          if (pct < 100) return;
          stopStartProgressAnimation();
        })
        .catch(() => {});
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
      syncDomainsFromState(state.include_domains || []);
      const draftContent = state.final_draft || "";
      if (draftContent) {
        draftEditorEl.value = draftContent;
      } else {
        draftEditorEl.value = "";
      }
      renderPublishedDrafts(state.published_drafts || []);
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

    async function loadDraft(idx) {
      const threadId = threadIdEl.value.trim();
      if (!threadId) return;
      setActivity(`Loading draft ${idx}...`);
      const res = await fetch(`/api/published-draft/load/${encodeURIComponent(threadId)}/${idx}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || "Failed to load draft");
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
        alert(data.detail || "Failed to delete draft");
        return;
      }
      applyState(data.state, data.history || []);
    }
    window.deleteDraft = deleteDraft;

    async function copyDraftMarkdown() {
      const text = draftEditorEl.value || "";
      if (!text || text === "No draft yet.") {
        alert("No draft available to copy yet.");
        return;
      }
      await navigator.clipboard.writeText(text);
      alert("Draft copied to clipboard.");
    }

    async function openLinkedIn() {
      const text = draftEditorEl.value || "";
      if (!text || text === "No draft yet.") {
        alert("No draft to post. Generate a draft first.");
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
        alert("Draft is empty. Write something before saving.");
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
      alert(data.message || "Thread reset.");
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
      const progInterval = setInterval(() => {
        const tid = threadIdEl.value.trim();
        if (!tid) return;
        fetch(`/api/progress/${encodeURIComponent(tid)}`)
          .then(r => r.json())
          .then(d => {
            if (d.ok && d.pct) {
              progressFillEl.style.width = d.pct + "%";
              progressPctEl.textContent = d.pct + "%";
              if (d.message) setActivity(d.message);
            }
          }).catch(() => {});
      }, 1200);
      const payload = {
        thread_id: threadIdEl.value.trim(),
        selected_article_id: selectedIdEl.value.trim(),
        human_feedback: feedbackEl.value.trim() || null,
      };
      const res = await fetch("/api/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      clearInterval(progInterval);
      const data = await res.json();
      if (!res.ok) {
        progressSectionEl.style.display = "none";
        throw new Error(data.detail || "Failed to resume flow");
      }
      applyState(data.state, data.history || []);
    }

    async function withUiErrors(fn) {
      try {
        await fn();
      } catch (err) {
        stopStartProgressAnimation();
        alert(err.message || String(err));
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
    updateModelList("analyst-provider", "analyst-model");
    updateModelList("writer-provider", "writer-model");
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
        graph_app.invoke(Command(resume=resume_payload), config=config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

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


@web_app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}
