# Processing Details: Article Pipeline

This file reflects the current implementation as of 2026-08-30 (branch `main`, commit `9d2b714`). All prior work (Phases 1-5, sessions `db626376` and `c3152374`) is now **committed and merged to `main`** via PRs #1 and #2. §15 and §16 describe that prior work. §17 describes the writer-prompt improvements and hybrid paywall exclusion added in session `6830d7a1` (2026-08-30). Status: **PAUSED, not complete** — two files remain uncommitted (`docker-compose.yml` volume mount + `writer_examples.txt`). See `SESSION_CONTINUITY.md` and `sessions/2026-08-30_6830d7a1.md` for the full handoff. Sections 1-14 describe the state as of the original commit `64810ce`; where later sections change something described earlier, the later section is authoritative.

## 1) Purpose and Current Pipeline

`article-pipeline` is a LangGraph HITL workflow for technical-news discovery and LinkedIn drafting:

1. `scout` gathers recent articles (concurrently, across all selected domains).
2. `analyst` ranks top candidates on a multi-axis relevance score.
3. `approval` interrupts for human selection.
4. `author` drafts the final post (streamed) and runs a factuality check.
5. `edit_approval` interrupts for publish/edit/pick-another/done.

Graph topology in `graph.py`:
- `START -> scout -> analyst -> (approval or END) -> author -> edit_approval -> (approval, edit_approval, or END)`
- `SqliteSaver` checkpointing (`checkpoints.db`), resume by reusing `thread_id`. Survives server restarts.
- This is a personal localhost workflow; do not add cloud-scale infra unless explicitly requested.

**Checkpointer gotcha (fixed 2026-08-18):** `build_graph()` connects with `sqlite3.connect(db_path, check_same_thread=False)` and constructs `SqliteSaver(conn)` directly. Do **not** switch this back to `SqliteSaver.from_conn_string(db_path).__enter__()` — that pattern discards the context-manager generator, which gets garbage-collected almost immediately after `build_graph()` returns, closing the connection before the graph is ever invoked. This broke every single graph invocation for the entire scheduler/cache/streaming work cycle until it was caught during a live-smoke-test verification pass. See `AUDIT_REPORT_2026-08-18.md` for the full incident writeup.

## 2) Key Implementation Shift (Important)

Scout no longer uses Tavily anywhere — the Tavily/URL-enrichment code path was fully deleted on 2026-08-18 (it was already unreachable dead code referencing dependencies removed from `requirements.txt`; ~380 lines removed from `graph.py`).

Current ingestion is **domain-routed dual strategy, fetched concurrently**:
- RSS-first for domains with known feeds.
- Google News RSS fallback for no-feed and unknown domains.
- Fetches run in a `ThreadPoolExecutor` (`SCOUT_MAX_WORKERS`, default `6`) instead of sequentially — this is the single biggest scout-latency improvement so far (was 30-90s sequential, now a few seconds for a dozen-plus domains).
- Each feed response is cached (`feed_cache.py`, SQLite, default 15-minute TTL) to avoid re-fetching the same feed on back-to-back runs.

This routing is defined in `graph.py` via `DOMAIN_INGESTION_MAP`.

Examples:
- RSS: `openai.com`, `techcrunch.com`, `github.blog`, `langchain.com`, `news.ycombinator.com`, etc.
- Google News RSS fallback: `anthropic.com`, `a16z.com`, `tldr.tech`, `alphasignals.com`, `mybrandi.ai`, `uberall.com`, and any user-added unknown domain.
- VentureBeat uses direct RSS: `https://venturebeat.com/category/ai/feed`.
- Direct RSS was also added for `searchengineland.com`, `searchenginejournal.com`, `marketingbrew.com`, `kopp-online-marketing.com`, and `semrush.com`.

## 3) File Structure (Core)

Root: `/home/toufic/Source/article-pipeline`

- `graph.py` - state schema, scout/analyst/approval/author nodes, routing, model selection, checkpointing.
- `settings.py` - env-backed config helpers (topic, age limits, volume caps, factuality toggle, Ollama model list).
- `main.py` - CLI start/resume flow.
- `ui.py` - FastAPI + HTML/JS UI; all `/api/*` routes.
- `preflight.py` - env and local service checks.
- `progress.py` - in-memory per-thread progress + token-stream tracking (thread-safe, module-level dict).
- `draft_store.py` - SQLite persistence for published drafts (`drafts.db`).
- `feed_cache.py` - SQLite TTL cache for raw RSS/Google News feed responses (`cache.db`).
- `domain_store.py` - SQLite store for the UI's curated domain list, shared with the scheduler (`domains.db`).
- `scheduler.py` - APScheduler cron job that runs scout+analyst unattended and stops at the `approval` interrupt.
- `scheduled_store.py` - SQLite persistence for scheduled-run candidates/review state (`scheduled_runs.db`).
- `emailer.py` - SMTP HTML digest sender for scheduled runs.
- `cost_tracker.py` **(new, §15.1, uncommitted)** - SQLite LLM token/cost logging (`costs.db`).
- `tests/test_graph_edges.py` - stdlib tests for draft review, URL safety, topic matching, analyst prompt edges, the heuristic prefilter (§15.6), and author personas (§15.5).
- `tests/test_new_modules.py` - stdlib tests for feed-cache round-tripping, domain-store CRUD, the checkpointer smoke test, and draft version history (§15.3).
- `tests/test_cost_tracker.py` **(new, uncommitted)** - stdlib tests for `cost_tracker.py`.
- `requirements.txt` - dependencies (now includes `langgraph-checkpoint-sqlite`, `apscheduler`; `langchain-tavily`/`langchain-community` removed).
- `.env.example` - env template.
- `AGENTS.md` - condensed agent-facing reference (architecture, entrypoints, gotchas).
- `AUDIT_REPORT.md` - first-pass system/UX/feature audit (superseded by the v2 below on any conflict).
- `AUDIT_REPORT_2026-08-18.md` - v2 audit: verifies every v1 recommendation against the actual implementation, documents 9 numbered bugs found (including the checkpointer bug above), and logs every fix applied.
- `future-features.md`, `README.md`, `instructions.md`, `prd.md`, `spec.md` (older planning docs — may still describe pre-2026-08 Tavily-first behavior).

## 4) Data Models and Contracts

## 4.1 Python Models (`graph.py`)

`Article` (TypedDict):
- `id`, `title`, `url`, `source`, `published_at`, `summary`, `relevance_score`

`AgentState` (TypedDict, total=False):
- `raw_articles`, `curated_candidates`, `selected_article_id`, `final_draft`
- `workflow_status`, `human_feedback`, `scout_debug`
- `published_drafts` — appended to (not replaced) on each `publish` action
- runtime controls: `topic`, `include_domains`, providers/models

Note: `raw_articles` uses reducer `operator.add`.

Analyst structured schema (multi-axis scoring, added 2026-08):
- `AnalystPick(id, relevance_score, contrarian_value, technical_depth, debate_potential, timeliness, source_credibility)` — `relevance_score` is meant to be the LLM's own weighted average of the five 0-10 sub-scores (weights 0.30/0.25/0.20/0.15/0.10, given in the prompt).
- `AnalystResponse(picks: List[AnalystPick])`

## 4.2 API Request Models (`ui.py`)

- `StartRequest` — gained `persona: str = "cto_phd"` in §15.5 (uncommitted)
- `ResumeRequest`
- `RefineRequest` — thread_id, instruction, current_draft (powers the Quick Refine toolbar)
- `ProviderHealthRequest`
- `DomainsRequest` — domains, disabled (powers `/api/domains` sync between the UI and the scheduler)
- `WebhookStartRequest` **(new, §15.7, uncommitted)** — topic, include_domains, analyst_provider, writer_provider, analyst_model, writer_model, persona (all `Optional`; powers `POST /api/webhook/trigger`)

## 5) Scout Details

### 5.1 RSS path

Uses `feedparser` to parse feed entries. Each `_fetch_source` call (inside `scout_node`'s `ThreadPoolExecutor`) first checks `feed_cache.get(feed_url)`; on a miss it fetches raw feed text via `_fetch_feed_raw()` (a dedicated `urllib` fetch, capped at 15MB — large enough for big feeds like arXiv's ~4MB `cs` feed without truncating mid-XML) and caches that **raw text**, not the parsed object. (Caching `str(parsed_feed)` was a real, since-fixed bug — `feedparser.parse()` cannot re-parse the Python repr of its own output; every cache hit was silently returning zero articles. See `AUDIT_REPORT_2026-08-18.md` Bug 1.)

Per-entry date extraction checks:
- parsed fields (`published_parsed`, `updated_parsed`, etc.)
- text fields (`published`, `updated`, `created`, `dc_date`)
- text scan fallback in title/summary/link

**Topic filtering**:
- `_topic_keywords()` tokenizes the topic into significant keywords (lower-cased, stop-words removed, no minimum length gate so `"AI"`, `"ML"`, `"QA"` survive).
- `_topic_matches()` checks each keyword as a word-boundary regex (`\bkeyword\b`) against the entry's title and summary.
- Entries that do not match are dropped with reason `topic_mismatch`.
- The topic is extracted from `state["topic"]` (UI textarea or CLI `--topic`).

Filters:
- drop missing title+url
- drop missing date when `ALLOW_UNDATED_ARTICLES=false`
- drop older than `MAX_ARTICLE_AGE_DAYS`
- drop topic mismatch (if topic keywords present)

### 5.2 Google News RSS fallback

Uses `_build_google_news_rss_url()` to generate a Google News RSS search URL for no-feed and unknown domains.

Query shape:
- `site:<domain>`
- `when:<MAX_ARTICLE_AGE_DAYS>d`

Google News fallback intentionally does **not** include topic terms in the remote query (over-constrained queries previously produced `0/0` results). Google News RSS entries flow through the same `_normalize_rss_entries()` path as direct RSS, using `ingestion_mode="google_news"` for debug/progress labels, and go through the same `feed_cache` layer as direct RSS.

**Topic filtering**: applied client-side after retrieval via the same `_topic_matches()` check as direct RSS.

> Note: the older Tavily-based URL-enrichment fallback (`_enrich_published_at_from_url`, `_http_fetch_text`, `_extract_date_from_html`, etc.) was deleted 2026-08-18 as dead code — it hadn't been reachable from the active scout path since the Tavily removal, and its fallback import referenced `langchain_community`, which is no longer a dependency. `_is_public_http_url()` was kept (it's independently tested and a reasonable safety primitive to keep around for any future URL-fetching code) even though it currently has no production caller.

### 5.3 Volume Controls

From `settings.py`:
- `RSS_MAX_ITEMS_PER_FEED` (default `25`)
- `SCOUT_MAX_TOTAL_ARTICLES` (default `80`)
- `SCOUT_MAX_WORKERS` (default `6`) — size of the `ThreadPoolExecutor` used to fetch all selected domains concurrently.

Applied in scout:
- cap entries per feed
- dedupe URLs/titles
- sort by recency
- global cap to avoid huge candidate sets/cost spikes

### 5.4 Feed cache (`feed_cache.py`)

SQLite-backed (`cache.db`, path overridable via `CACHE_DB_PATH`), keyed by `sha256(feed_url)`, TTL via `CACHE_TTL_SECONDS` (default `900` = 15 min). `get()` returns `None` on a miss or an expired entry; `set()` upserts. Verified via `tests/test_new_modules.py::FeedCacheRoundTripTests` — caching a real feed string and re-parsing it with `feedparser` must reproduce the original entries.

### 5.5 Live source logging

During a scout run, `progress.py` maintains a `source_log` list on a per-thread basis. Each source (domain) is appended as it is queried, creating an ordered, real-time log of all ingestion calls. The UI renders these as pills under the **Sources Queried** card, updated every 1.2 seconds via `/api/progress`. `progress.py` also tracks token-stream state (`stream_tokens`, `stream_done`) for the author-node SSE stream (see 6.3).

## 6) Analyst & Author: Prompting, Streaming, Factuality

### 6.1 Prompt Size Safeguards

In `graph.py`:
- `ANALYST_MAX_ARTICLES = 20`
- `ANALYST_SUMMARY_MAX_CHARS = 260`
- `AUTHOR_SUMMARY_MAX_CHARS = 1600`

Analyst receives a bounded, recency-prioritized subset to reduce provider 400 errors on message length.

### 6.2 Analyst: multi-axis scoring

The analyst prompt scores each article 0-10 on five axes and computes a weighted `relevance_score`:
- Contrarian/Insight Value (0.30)
- Technical Depth (0.25)
- Debate Potential (0.20)
- Timeliness (0.15)
- Source Credibility (0.10)

The prompt includes explicit AUTO-REJECT criteria (press releases, funding announcements, generic trend pieces, clickbait, thin/paywalled summaries) and STRONGLY PREFER criteria (contrarian angles, tradeoffs, benchmarks, opinionated stances), plus one worked high-scoring and one worked low-scoring example. **Note:** this filtering is prompt-only — there is no pre-LLM heuristic filter, so it doesn't reduce the article count (or cost) of the analyst call itself; it only affects which of the already-capped `ANALYST_MAX_ARTICLES` get a high score.

The recency sentence uses configured `MAX_ARTICLE_AGE_DAYS` instead of a hardcoded value, and the topic instruction reads `state["topic"]` rather than hardcoding a theme.

### 6.3 Author: streaming + factuality check

`author_node` streams the draft token-by-token via `writer_llm.stream()`, pushing each token into `progress.py`'s per-thread `stream_tokens` list. The UI opens an `EventSource` against `GET /api/stream/{thread_id}` (SSE), which polls `progress.py` every `SSE_POLL_INTERVAL_SECONDS` (0.15s) for up to `SSE_STREAM_TIMEOUT_SECONDS` (default `240`, configurable — must stay comfortably above `LLM_REQUEST_TIMEOUT` or the stream will report "done" before a slow model actually finishes).

After streaming completes, `author_node` runs `_verify_factuality()` — a second LLM call that asks the model to flag any claim in the draft not supported by the source summary. This is gated by `ENABLE_FACTUALITY_CHECK` (default `true`); disabling it saves one full LLM call per draft. Notes surface in the UI as an amber callout above the draft editor when non-empty and not exactly `"All claims verified."`

### 6.4 Analyst Structured Output Parsing

`_invoke_analyst_structured` has a two-layer retry:

1. **Primary path**: `analyst_llm.with_structured_output(AnalystResponse)`.
2. **Fallback path**: on any exception, re-prompt with an explicit JSON schema instruction, then parse via `_extract_json_payload` (strips markdown fences, tries direct `json.loads`, falls back to extracting the first `{...}` block, wraps a bare `[...]` array as `{"picks": [...]}`).

### 6.5 Inline draft refinement (Quick Refine toolbar)

`POST /api/refine` takes `{thread_id, instruction, current_draft}` and re-invokes the writer LLM with a targeted instruction, returning the revised draft text only (no explanations/markdown). Five buttons in the UI send fixed instructions: "Make Hook Punchier," "Shorten," "More Technical," "Stronger CTA," "Fix Grammar." Each click is a full LLM call and overwrites the draft editor — **but as of §15.3 (2026-08-26, uncommitted), every refine is snapshotted to draft version history first**, so it's no longer destructive; see §15.3 for the undo/version-history mechanism.

## 7) Debug/Observability Shape

`scout_debug` includes:
- `query`, `include_domains`, `effective_domain_mode = "domain_routing_map"`
- `routing`: `rss_domains`, `google_news_domains`, `unknown_domains`, `fallback_domains`
- `stats`: counts (`records_count`, `kept_count`, `dropped_count`), recency settings, `max_total_articles`, `sample_urls`, `url_audit` entries with drop reasons and date-source details
- `sources` (per-feed/per-domain stats)
- `errors` (ingestion/parse/API issues)
- `factuality_notes` (set by `author_node` after the factuality check, when enabled)

## 8) UI State and UX

`ui.py` provides:
- Start / provider-health / resume / refine / state / history / domains / scheduled-runs endpoints
- Candidate cards are click-to-select with a radio input and visual highlight (`pickId()`), not a raw ID text field
- **Sources Queried** card — live-updating pill display of each domain as the scout queries it (1.2s poll via `/api/progress`)
- Live-streamed draft editor during author generation (SSE)
- Quick Refine toolbar (5 one-click LLM re-prompts)
- Factuality-check callout
- `Dropped Articles Audit`, `Domain diagnostics`
- Button busy states (`Starting...`, `Testing...`, `Generating...`, etc.)
- Progress panel with step statuses: Scout, Curate, Await Approval, Author Draft, Draft Approval
- **Scheduled Runs** card — lists automated runs (topic, triggered/emailed/reviewed timestamps), click to load candidates, "Mark Reviewed" button per row

### 8.0 Responsive layout

`.row` uses `flex-wrap: wrap`; grids use `minmax(min(..., 100%), 1fr)`; progress row uses `auto-fit` `minmax`; cards/text/JSON blocks use `min-width: 0` and `overflow-wrap: anywhere`. Adapts cleanly to narrow viewports and long domain names.

### 8.1 Resume flow

`/api/resume` is the single entry point for both `Approve + Generate Draft` and `Edit`/`Publish`/`Done` actions:
- If `payload.action` is provided, it's forwarded verbatim (merged with `edited_draft`/`human_feedback`).
- If only `selected_article_id` is provided (the "Approve + Generate Draft" path), the server checks the checkpoint's `next` nodes: if `edit_approval` is next, it first issues `Command(resume={"action": "pick_another"})` to re-enter approval, then resumes with the real selection — a deterministic "regenerate draft" path regardless of prior state.

The button label is `Approve + Generate Draft` (`ui.py`); pressing it after `edit_approval` reopens approval and produces a new draft.

### 8.2 Draft Review Safety

Centralized in `_apply_draft_review_action()` (`graph.py`). Allowed actions: `publish`, `edit`, `pick_another`, `done`. Unknown actions raise `ValueError`; `edit` requires non-empty `edited_draft`; publishing only happens for explicit `action="publish"`. `main.py`'s CLI `--action` choices now include `"done"` (previously missing, out of sync with the graph's actual action set).

### 8.3 Domain configuration — now server-synced

The UI still uses `localStorage` (`article_pipeline_domains`, `article_pipeline_domains_disabled`) as its offline-first source, but every checkbox toggle and "Add Domain" action also pushes the full enabled/disabled state to `domain_store.py` via `POST /api/domains`. On page load, `hydrateDomainsFromServer()` fetches `GET /api/domains` and merges it into the local state. **This is what the scheduler reads** — `scheduler.py` prefers `domain_store.get_enabled_domains()` and only falls back to the static `SCHEDULER_DOMAINS` env var if the store is empty (i.e. before the UI has ever synced once). Before this fix, scheduled runs used a domain set completely disconnected from whatever the user had curated in the browser.

## 9) Scheduled Runs & Email Digest

`scheduler.py` runs an `APScheduler` `BackgroundScheduler` (enabled via `SCHEDULER_ENABLED=true`, cron via `SCHEDULER_CRON`) that invokes the compiled graph on a dedicated `thread_id` (`scheduled-YYYY-MM-DD-HHMM`). Because `approval_node` always interrupts, `app.invoke()` returns as soon as scout+analyst finish and the graph is paused waiting for a human selection — the job never resumes it itself.

The graph instance used by the scheduler is built **once**, lazily, and reused across every job run (`_get_graph_app()` in `scheduler.py`) — building fresh per job was leaking a SQLite connection on every tick (fixed 2026-08-18).

After each run:
- `scheduled_store.store_run()` persists `run_id`, `thread_id`, `topic`, and the curated candidates to `scheduled_runs.db`.
- If candidates exist, `emailer.send_digest()` sends an HTML email (SMTP, configured via `SMTP_*`/`EMAIL_*` env vars) with a ranked candidate table, a **"Review & Generate Draft"** link (`{BASE_URL}/?run={run_id}`), and a **"Skip This Batch"** link (`{BASE_URL}/api/scheduled-runs/{run_id}/skip`, a `GET` endpoint since email clients only support plain links — it just marks the run reviewed).

**UI side of the loop** (`ui.py`):
- `GET /api/scheduled-runs` / `GET /api/scheduled-runs/{run_id}` list/load runs.
- `POST /api/scheduled-runs/{run_id}/review` and `GET /api/scheduled-runs/{run_id}/skip` both mark a run reviewed (the GET variant exists purely so the email link works without JS).
- Clicking a run in the **Scheduled Runs** card (or landing on `/?run={run_id}` via the email link — handled by a `URLSearchParams` check on page load) calls `loadScheduledRun()`, which sets the Thread ID field to that run's `thread_id`, resets the selected-article field, and renders the candidates. **This thread-ID sync was missing until 2026-08-18** — before the fix, every action after loading a scheduled run operated on whatever thread ID happened to already be in the box, so the review flow could never actually reach the paused thread (`AUDIT_REPORT_2026-08-18.md` Bug 2).

**As of §15.7 (2026-08-26, uncommitted)**: runs can also be triggered externally via `POST /api/webhook/trigger` (secret-gated via `WEBHOOK_SECRET`), not just by the cron schedule — both entry points now share the same underlying `scheduler.run_scout_analyst_job()` function, so a webhook-triggered run lands in this exact same Scheduled Runs panel/email flow. See §15.7 for full detail.

## 10) Environment and Dependencies

Env keys in active use:
- `ARTICLE_PIPELINE_DEFAULT_TOPIC`, `MAX_ARTICLE_AGE_DAYS`, `ALLOW_UNDATED_ARTICLES`
- `RSS_MAX_ITEMS_PER_FEED`, `SCOUT_MAX_TOTAL_ARTICLES`, `SCOUT_MAX_WORKERS`
- `NEWS_SOURCE_DOMAINS`
- `LLM_REQUEST_TIMEOUT` (default `60`), `LLM_MAX_RETRIES` (default `2`) — applied to every provider in `_get_chat_model()`
- `CHECKPOINT_DB_PATH` (default `checkpoints.db`)
- `CACHE_DB_PATH` (default `cache.db`), `CACHE_TTL_SECONDS` (default `900`)
- `DOMAIN_STORE_DB_PATH` (default `domains.db`)
- `SCHEDULED_RUNS_DB_PATH` (default `scheduled_runs.db`)
- `ENABLE_FACTUALITY_CHECK` (default `true`)
- `SSE_STREAM_TIMEOUT_SECONDS` (default `240`)
- provider keys/models (`OPENAI_*`, `GOOGLE_*`, `GROQ_*`, `OLLAMA_*`) — note `GEMINI_MODEL` default was updated from the now-retired `gemini-2.0-flash` to `gemini-3.6-flash` on 2026-08-26, see §15
- `OLLAMA_API_KEY` for cloud-hosted Ollama providers; `OLLAMA_MODEL_OPTIONS` dynamically populates the UI model dropdown
- **`COST_TRACKER_DB_PATH`** (default `costs.db`) — new, §15.1
- Scheduler: `SCHEDULER_ENABLED`, `SCHEDULER_CRON`, `SCHEDULER_TOPIC`, `SCHEDULER_DOMAINS` (fallback only, see 8.3), `SCHEDULER_ANALYST_PROVIDER`, `SCHEDULER_WRITER_PROVIDER`, `SCHEDULER_ANALYST_MODEL`, `SCHEDULER_WRITER_MODEL`, **`SCHEDULER_PERSONA`** (new, §15.5, default `cto_phd`)
- **Webhook** (new, §15.7): `WEBHOOK_SECRET` (unset = endpoint disabled/501), `WEBHOOK_TOPIC`, `WEBHOOK_ANALYST_PROVIDER`/`WEBHOOK_WRITER_PROVIDER` (default `ollama`), `WEBHOOK_ANALYST_MODEL`/`WEBHOOK_WRITER_MODEL`
- Email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`, `BASE_URL`

`requirements.txt` additions: `langgraph-checkpoint-sqlite`, `apscheduler`. Removed: `langchain-tavily`, `tavily-python`, `langchain-community` (Tavily search is no longer used anywhere, including in dead code).

## 11) Deployment and Infrastructure

- **GitHub**: `https://github.com/guzziride/article-pipeline`
- **Docker**: `Dockerfile` (Python 3.12-slim), `docker-compose.yml`. **As of §15.10 (2026-08-26, uncommitted)**, all six SQLite files are volume-mounted: `drafts.db`, `cache.db`, `checkpoints.db`, `domains.db`, `scheduled_runs.db`, `costs.db`. Accepted caveat (pre-existing, not new): all six use WAL mode, so `-wal`/`-shm` sidecars aren't carried by a single-file bind mount — see §15.10.
- **`.gitignore`**: covers `drafts.db*`, `cache.db*`, `checkpoints.db*`, `scheduled_runs.db*`, `domains.db*`, and (as of §15.1, uncommitted) `costs.db*`.

## 12) Local Tests

Tests use Python stdlib `unittest`; no pytest dependency required. **32 tests total** across three modules (as of the uncommitted §15 work — was 17 at commit `64810ce`):

- `tests/test_graph_edges.py` — draft review actions, URL safety, Google News URL building, topic matching, analyst prompt recency wording, malformed-resume rejection, plus (§15, new) `HeuristicPrefilterTests` (5 tests) and `AuthorPersonaTests` (2 tests).
- `tests/test_new_modules.py` — feed-cache round-trip (cached raw text must re-parse into the same entries; regression test for the since-fixed cache-corruption bug), domain-store enable/disable CRUD, a `build_graph()` + `get_state()` smoke test (regression test for the since-fixed checkpointer-closed-by-GC bug), plus (§15, new) `DraftVersionTests` (3 tests).
- `tests/test_cost_tracker.py` **(new, §15.1, uncommitted)** — 5 tests for `cost_tracker.py`'s pricing/aggregation logic.

Note: `analyst_node()` now runs the §15.6 heuristic prefilter unconditionally. Any test that calls it directly needs fixture article summaries **≥ 40 characters**, or they'll be silently dropped before reaching the (possibly mocked) LLM call — this bit two pre-existing tests during the §15 work and is easy to hit again without realizing why a test is failing.

Run:

```bash
# Local — full suite
.venv/bin/python -m unittest discover -s tests -p "test_*.py"

# In container
docker compose exec -T article-pipeline python -m unittest discover -s tests -p "test_*.py"
```

**No coverage exists for**: `scheduler.py`/`emailer.py` end-to-end, the SSE stream endpoint, `/api/refine`, or any of the frontend JS (thread-ID sync on scheduled-run load, deep-link `?run=` handling, Mark Reviewed button, domain server-sync). These were all verified manually via live smoke-testing during the 2026-08-18 fix pass, not by automated tests.

## 13) Current Known Issues / Open Items

**Resolved by §15 (2026-08-26, uncommitted — see that section for detail):** draft version history now exists (§15.3); cost/token visibility now exists (§15.1); pre-LLM heuristic filtering now exists (§15.6); `docker-compose.yml` now volume-mounts all six SQLite files (§15.10); `alert()` replaced with toasts (§15.2); export formats added (§15.4); persona switching added (§15.5); hashtag library added (§15.8); webhook trigger added (§15.7).

**Still genuinely open:**
- No JS/frontend test harness exists at all — every UI-side fix across both this session and the prior one was verified by manual code review plus live API smoke-testing, not automated tests. This now also explicitly applies to `/api/webhook/trigger` — see §15.7 for why an automated test wasn't added and what it would take.
- `RUN_HISTORY` in `ui.py` is still an in-memory dict — lost on restart, unlike checkpoints/drafts/versions/costs which are now durable.
- `spec.md` and older planning docs may still describe pre-2026-08 Tavily-first behavior.
- No CI/linter/formatter is configured; verification is manual local `unittest` plus CLI/UI runs.
- **LinkedIn API direct-publish** — deferred, not built; user explicitly chose to skip for now (§15.11). Don't build without asking again.
- **Phase 5** (persistent user style profile/feedback loop, multi-format output beyond LinkedIn posts, topic/source performance dashboard) — not started, not designed (§15.11). See `FEATURE_BACKLOG_PLAN.md`.
- Nothing from Phases 1-4 (§15) has been committed yet — it's all sitting in the working tree. See `SESSION_CONTINUITY.md` and `sessions/2026-08-26_db626376.md` for the exact uncommitted-file list and why.

## 14) Author Prompt Format

`author_node()` builds the entire writer prompt as a single triple-quoted f-string. The prompt is intentionally a single editable block to make persona/voice tweaks easy. Substitutions:
- Persona block + voice rules + structure + style injection + a worked example output are all static text.
- Article fields (`title`, `url`, `published_at`, `summary`, `relevance_score`) are interpolated per article.
- `summary` is truncated to `AUTHOR_SUMMARY_MAX_CHARS` (1600) to avoid provider 400s.
- `human_feedback` is appended as a final `Human feedback: {feedback or 'None'}` line — free-form, no structural parsing.
- The model is invoked via `writer_llm.stream()` (see §6.3); the concatenated streamed content becomes `final_draft`.

## 15) Feature Backlog Phases 1-4 (session `db626376`, 2026-08-26) — PAUSED, uncommitted

Everything in this section is implemented and live-verified, working-tree-only (not committed). Test suite: 32 stdlib `unittest` tests (up from 17), all passing as of the last run — `tests/test_graph_edges.py`, `tests/test_new_modules.py`, `tests/test_cost_tracker.py` (new).

### 15.1 Cost tracking (`cost_tracker.py`, new file)

SQLite-backed (`costs.db`, WAL mode, same connection pattern as `domain_store.py`), one `usage` table: `id, ts, thread_id, node, provider, model, input_tokens, output_tokens, cost_usd, estimated`. `PRICING` dict gives approximate $/1M-token input/output rates for the models already referenced in `_get_chat_model` (`gpt-4o`, `gpt-4o-mini`, `gemini-3.6-flash`, `gemini-2.5-flash`, `gemini-2.5-pro`, groq's llama variants); `ollama` is always `$0`; unrecognized provider/model pairs fall back to a conservative default rate. **Not billing-accurate** — for relative visibility only, said explicitly in the module docstring.

`graph.py` gained two small helpers used everywhere cost is logged:
- `_capture_usage(response, prompt, output_text) -> (input_tokens, output_tokens, estimated)`: reads `response.usage_metadata` when present and non-empty (populated by `langchain-openai`/`langchain-google-genai`/`langchain-groq`/`langchain-ollama` on the returned `AIMessage` when the provider reports it); else falls back to `len(text)//4` character estimates for both prompt and output, flagged `estimated=True`.
- `_resolve_model_name(llm, model_override)`: `getattr(llm, "model_name", None) or getattr(llm, "model", None) or model_override or "unknown"` — providers store the resolved model name under different attribute names.

Wired into all 4 LLM call sites:
- `analyst_node` — **always** estimated (`estimated=True`), never attempts real `usage_metadata`. Deliberate: `with_structured_output`'s primary path doesn't expose the raw `AIMessage`, and restructuring that (e.g. `include_raw=True`) was judged higher-risk than it was worth for this pass. Logged under node `"analyst"`.
- `author_node` — accumulates streamed chunks via `merged_chunk = chunk if merged_chunk is None else merged_chunk + chunk` (LangChain `AIMessageChunk` supports `+`), so the final merged chunk can carry real `usage_metadata` when the provider streams it (confirmed working live for Gemini). Logged under `"author_draft"`.
- `_verify_factuality` — real usage from the direct `.invoke()` response. Logged under `"factuality"`. Signature gained `thread_id`, `provider`, `model` params (previously just `draft, article, llm`).
- `ui.py`'s `/api/refine` — real usage from the direct `.invoke()` response. Logged under `"refine"`.

New endpoints: `GET /api/costs/{thread_id}` (per-node breakdown + total for one thread), `GET /api/costs/summary?hours=24` (rollup across all threads). New UI element `#cost-summary` (a single line, populated by `refreshCostSummary()`, called at the end of `applyState()`) — intentionally not a dashboard; that's the separate, larger Phase 5 "topic/source performance dashboard" item.

**Live verification performed**: full flow (scout → analyst → author → factuality → refine) against real Gemini API calls, confirmed `estimated=False` (real token counts) for author/factuality/refine and `estimated=True` for analyst, confirmed `/api/costs/{thread_id}` aggregates correctly across all 4 nodes.

### 15.2 Toast notifications (`ui.py`)

New `#toast-container` div + `.toast`/`.toast-error`/`.toast-success`/`.toast-info` CSS + `toast(message, type="info", duration=4000)` JS function (auto-dismiss with fade, click-to-close). All 11 pre-existing `alert()` call sites replaced. The 2 `confirm()` calls (delete draft, reset thread) are **unchanged** — deliberate, those are blocking decisions not notifications.

### 15.3 Draft version history (`draft_store.py`, `graph.py`, `ui.py`)

New table in `draft_store.py` (separate from the existing `published_drafts` table, which only tracks *published* drafts):
```sql
CREATE TABLE draft_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    draft TEXT NOT NULL,
    source TEXT NOT NULL,       -- "author" | "refine: <instruction>" | "manual_edit"
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
```
New functions `add_version(thread_id, draft, source)`, `get_versions(thread_id) -> List[Dict]` (ordered by `id ASC`). `delete_all_for_thread()` extended to also clear `draft_versions` (previously only cleared `published_drafts`) — this is what `/api/reset/{thread_id}` calls, so a thread reset now correctly wipes version history too (verified live).

Snapshot points (one version row per distinct draft state — a linear, append-only log, never truncated):
- `graph.py` `author_node`, right after `full_draft` is generated: `draft_store.add_version(tid, full_draft, "author")`.
- `ui.py` `/api/refine`, after computing `refined`: `draft_store.add_version(payload.thread_id, refined, f"refine: {payload.instruction}")`.
- `ui.py` `/api/resume`, only when `payload.action == "edit" and payload.edited_draft`: `draft_store.add_version(payload.thread_id, payload.edited_draft, "manual_edit")`.

`_state_snapshot()` in `ui.py` now returns `draft_versions` (full list via `draft_store.get_versions(thread_id)`). New UI: a `<details>` "Version History" panel below the draft editor, rendered by `renderDraftVersions(versions)` (newest first) — clicking any entry calls `loadDraftVersion(idx)`, which loads that version's text back into `draftEditorEl`. This is the entire undo mechanism: there's no separate "Undo" button, clicking the second-from-top entry *is* undo. Doesn't delete or truncate anything — refining again after loading an old version just appends a new version on top.

**Live verification performed**: generated a draft (persona `practitioner_engineer`), confirmed 1 version recorded (`source="author"`); refined it, confirmed 2 versions (`"author"`, `"refine: ..."`); confirmed `/api/reset/{thread_id}` empties `draft_versions` for that thread.

### 15.4 Export formats (`ui.py`, frontend-only)

Two new buttons, "Download .txt" / "Download .md", next to the existing Copy/LinkedIn buttons. `downloadDraft(filename, mimeType)`: builds a `Blob` from `draftEditorEl.value`, `URL.createObjectURL`, a temporary `<a download>` element, `.click()`, `URL.revokeObjectURL`. No backend involvement — the draft is already client-side.

### 15.5 Persona switching (`graph.py`, `ui.py`)

`graph.py` gained a `PERSONAS: Dict[str, Dict[str, str]]` module-level dict (defined right after `DRAFT_REVIEW_ACTIONS`) and `DEFAULT_PERSONA = "cto_phd"`. Three personas, each with `label`, `intro`, `voice`, `structure`, `example` string fields:
- `cto_phd` — the original, unchanged default voice (authoritative CTO/PhD).
- `startup_founder` — scrappy, growth-oriented, outcome/number-driven, allergic to hype.
- `practitioner_engineer` — first-person "I tried this so you don't have to," grounded, modest, plain language.

The previously-inline author prompt (VOICE/STRUCTURE/EXAMPLE blocks hardcoded in `author_node`) was extracted into a pure function `_build_author_prompt(article: Article, feedback: Optional[str], persona: str) -> str`, which looks up `PERSONAS.get(persona) or PERSONAS[DEFAULT_PERSONA]` (silent fallback for an unrecognized persona id — never raises). `author_node` now just calls this function instead of building an f-string inline.

`AgentState` gained `persona: str`. `ui.py`'s `StartRequest` gained `persona: str = Field(default="cto_phd")`, threaded into `/api/start`'s initial `graph_app.invoke()` call, `_state_snapshot()` (returns `persona`), and a new `<select id="persona-select">` dropdown in the "1) Start Flow" card (`startFlow()`'s payload includes it; `applyState()` sets the dropdown from `state.persona`). `/api/refine` in `ui.py` reads persona back off thread state (`values.get("persona") or DEFAULT_PERSONA`, imported from `graph` as `PERSONAS`/`DEFAULT_PERSONA`) and prefixes its own prompt with that persona's `intro` — no new request field needed, mirrors how `writer_provider`/`writer_model` already work for that endpoint.

`scheduler.py`'s `_run_scheduled_job` also gained persona support for the first time (`SCHEDULER_PERSONA` env var, default `"cto_phd"`) as part of the Phase 4a refactor (see §15.7).

**Live verification performed**: generated drafts with `startup_founder` and `practitioner_engineer` personas against real Gemini calls, confirmed the actual generated text matched each persona's distinguishing voice (e.g. `practitioner_engineer` output literally began "I tried building a lightweight agent..."); confirmed the persona carried through to `/api/refine`'s output too.

### 15.6 Pre-LLM heuristic prefilter (`graph.py`)

New constants and function, placed right before `_extract_domain`:
```python
PREFILTER_MIN_SUMMARY_CHARS = 40
_PREFILTER_TITLE_PATTERNS = [  # funding/press-release signals, high-precision by design
    r"\braises?\s+\$", r"\bseries\s+[a-e]\b", r"\bfunding\s+round\b",
    r"\b(closes?|secures?)\s+\$", r"\bannounces?\s+(a\s+)?(strategic\s+)?partnership\b",
    r"\bappoints?\b.{0,20}\b(ceo|cfo|coo|cto|president|chairman)\b",
]

def _heuristic_prefilter(articles: List[Article]) -> tuple[List[Article], Dict[str, int]]: ...
```
Drops an article if its summary is shorter than `PREFILTER_MIN_SUMMARY_CHARS` OR its title matches any pattern above; returns `(kept_articles, stats_dict)` where stats has `input_count`, `kept_count`, `dropped_thin_summary`, `dropped_press_release_pattern`. Wired into `analyst_node` **before** `_prepare_articles_for_analyst` — i.e. junk is filtered from the raw article set before the top-`ANALYST_MAX_ARTICLES`-by-recency cut, not after, so a press release doesn't waste one of the 20 analyst slots. Stats exposed as `scout_debug["analyst_prefilter"]` on all 3 of `analyst_node`'s return paths (early-empty-articles, empty-after-filter, and the normal final return).

**Critical bug found and fixed during live verification** (not caught by unit tests, which used synthetic short fixtures that happened to pass at any reasonable threshold): the *first* value tried was `PREFILTER_MIN_SUMMARY_CHARS = 150`, copied directly from the analyst prompt's own wording ("paywalled/thin summaries (<150 chars)"). Live-tested against the real `techcrunch.com` RSS feed (`scout_node` → `analyst_node`, no mocking) and found **8 of 10** genuinely legitimate, substantive articles got dropped — manually printed and inspected every dropped summary; none were press releases or actually thin, TechCrunch's RSS excerpts just naturally run ~90-190 characters. This is exactly the failure mode flagged as a risk before writing any code ("a bad heuristic could silently drop good candidates"). Fixed by lowering the threshold to `40`; re-verified live against the same feed with `dropped_thin_summary: 0` across all 10 articles. **Do not raise this threshold back toward 150 without first re-verifying against a real feed's actual summary-length distribution** — the failure is silent (candidates just never reach the analyst, no error, no warning) and was only caught by manually reading the dropped titles/summaries, not by any test or log threshold.

New tests: `tests/test_graph_edges.py::HeuristicPrefilterTests` (5 tests) — keeps substantial-summary/neutral-title articles, drops thin summaries, drops a funding-announcement title, drops an executive-appointment title, does not false-positive on "A New *Series* of Benchmarks" (the `series [a-e]` pattern requires a single-letter word boundary, not just the word "series"). Two pre-existing tests (`TopicAndPromptTests.test_analyst_prompt_uses_configured_max_age`, and the `AuthorPersonaTests`/`HeuristicPrefilterTests` fixtures generally) needed their fixture summaries lengthened past 40 chars, since the prefilter now runs unconditionally inside `analyst_node` and short test fixtures were getting silently filtered out before ever reaching the mocked LLM call — this is itself a useful signal: **any future test that calls `analyst_node()` directly needs a summary field ≥ 40 chars in its fixture, or the test will fail in a confusing way** (empty picks / `KeyError` on a captured-prompt dict that never got populated) that looks unrelated to the actual change being tested.

### 15.7 Webhook trigger (`scheduler.py`, `ui.py`)

`scheduler.py` refactored: extracted `run_scout_analyst_job(topic, include_domains, analyst_provider, writer_provider, analyst_model, writer_model, persona, run_id) -> List[Dict]` out of `_run_scheduled_job()`. This function does the actual work (build/reuse the graph via `_get_graph_app()`, `app.invoke()` the initial state, `scheduled_store.store_run()`, `emailer.send_digest()` if there are candidates) and is now called by **both** the cron job (`_run_scheduled_job`, which just resolves its env-var config and calls this) and the new webhook endpoint — meaning a webhook-triggered run gets exactly the same Scheduled-Runs-panel-and-email review flow as a cron-triggered one, for free, with no duplicated logic.

`ui.py` gained `WebhookStartRequest` (all fields `Optional`: `topic`, `include_domains`, `analyst_provider`, `writer_provider`, `analyst_model`, `writer_model`, `persona`) and `POST /api/webhook/trigger`:
- Reads `X-Webhook-Secret` header (FastAPI `Header(default="")`), compares against `os.getenv("WEBHOOK_SECRET")` via `secrets.compare_digest()` (constant-time, avoids timing side-channels on the comparison).
- **Returns `501`** if `WEBHOOK_SECRET` is unset/empty — the endpoint is disabled by default, safe for anyone who hasn't explicitly opted in.
- **Returns `403`** on a missing or mismatched secret.
- On success, resolves each payload field with a fallback chain: request payload value → `WEBHOOK_<FIELD>` env var → sensible default (mirrors the existing `SCHEDULER_<FIELD>` env-var pattern in `scheduler.py`). Domains fall back to `domain_store.get_enabled_domains()` if not provided. Generates `run_id = f"webhook-{now:%Y-%m-%d-%H%M%S}"`, calls `scheduler.run_scout_analyst_job(...)`, returns `{ok, run_id, thread_id, candidate_count}`.

New env vars (documented in `.env.example`): `SCHEDULER_PERSONA` (default `cto_phd`), `WEBHOOK_SECRET` (unset = disabled), `WEBHOOK_TOPIC`, `WEBHOOK_ANALYST_PROVIDER`/`WEBHOOK_WRITER_PROVIDER` (default `ollama`), `WEBHOOK_ANALYST_MODEL`/`WEBHOOK_WRITER_MODEL`.

**No automated test exists for this endpoint** — a deliberate scope decision, not an oversight. `ui.py` builds the graph and connects every SQLite store (`checkpoints.db` via `graph.build_graph()`, `domains.db`, `drafts.db`, `costs.db`, `scheduled_runs.db`) at **module import time**, each store module reading its own `DB_PATH` from an env var exactly once, at that module's first import, as a bare module-level constant (not a function call re-read later). `python -m unittest discover` imports every test file into one shared process; by alphabetical discovery order, `graph`/`draft_store`/`domain_store`/`cost_tracker` are already imported — with real, unisolated default paths — by `test_cost_tracker.py`/`test_graph_edges.py`/`test_new_modules.py` before a hypothetically-named `test_webhook.py` would ever run. Any test that imports `ui.py` in that shared discovery process would therefore silently create/touch the real app's SQLite files in the repo root as a side effect. (`BuildGraphCheckpointerTests` in `tests/test_new_modules.py` already works around exactly this problem, but only for `graph.build_graph()` called in isolation — not the full `ui.py` import chain, which additionally pulls in `scheduler`, `draft_store`, `domain_store`, `scheduled_store`, `cost_tracker`.) **To add a real automated test for this endpoint safely**, either: (a) give it its own dedicated test-runner invocation outside the shared `discover` process, or (b) refactor the `*_DB_PATH` module constants into a late-binding pattern (e.g. an explicit `init_db(path)` call rather than a bare module-level constant read once at import), so tests can redirect them regardless of import order.

**Live verification performed**: temporary server on port 3015 with `WEBHOOK_SECRET=test-secret-123` — confirmed `403` on missing `X-Webhook-Secret` header, `403` on a wrong secret, `200` plus a real run on the correct secret (verified the run appeared in `GET /api/scheduled-runs` and was resumable via `GET /api/state/{thread_id}` with `workflow_status="awaiting_approval"` and the requested `persona` correctly set). Separately, in a one-off isolated `TestClient` instance with every `*_DB_PATH` env var redirected to a tempdir *before* importing `ui` (not part of the committed test suite, a manual check only), confirmed the `501` disabled-by-default response when `WEBHOOK_SECRET` is unset.

### 15.8 Hashtag library (`ui.py`, frontend-only)

`HASHTAG_LIBRARY` — 16 hardcoded hashtags (`#AgenticAI`, `#MCP`, `#SaaS`, `#LLMOps`, `#DistributedSystems`, `#DevOps`, `#SoftwareArchitecture`, `#TechLeadership`, `#MachineLearning`, `#AIInfrastructure`, `#Observability`, `#PlatformEngineering`, `#Kubernetes`, `#OpenSource`, `#EngineeringLeadership`, `#ProductionAI`), rendered as clickable toggle chips in a `#hashtag-chips` div under the draft editor by `renderHashtagChips()`. `toggleHashtag(tag)` appends/removes the tag from `draftEditorEl.value` directly (string `.includes()`/`.split().join()`), then re-renders. Purely client-side text manipulation — does **not** touch the LLM prompt or `graph.py` at all; the author prompt's existing "Max 3 hashtags" constraint is unrelated and untouched.

### 15.9 A real bug in `ui.py`'s embedded JS, found during Phase 2 verification

The initial `toggleHashtag` implementation used single-backslash `\t` / `\n` / `\s` inside a JS regex/string, written directly into `ui.py`'s giant HTML/JS template — which is one large **non-raw** Python triple-quoted string (`html = """..."""`, no `r` prefix). Python interprets standard escape sequences in a non-raw string regardless of triple-quoting; `\t` and `\n` are both *valid* Python escapes, so they were silently converted into literal raw tab/newline **bytes** in the resulting string before it was ever served to the browser — not left as the two-character sequences `\t`/`\n` that JS needs to see. A raw, unescaped newline byte inside a JS regex or string literal is a JS syntax error (unterminated literal); this would have broken the hashtag-toggle feature outright in the browser despite the Python side importing with only a `SyntaxWarning` (for `\s`, which isn't a valid Python escape) and *zero* warning at all for `\t`/`\n` (which are valid Python escapes, so Python considers them intentional). Fixed by double-escaping to `\\t`, `\\n`, `\\s` in the Python source, so Python leaves a literal single backslash for the browser's JS engine to interpret. Verified by starting a server and `curl`-ing the actual served page, confirming the JS source in the HTTP response now contains real `\t`/`\n`/`\s` two-character escape sequences, not raw control-character bytes.

**Standing hazard for any future edit to `ui.py`**: any new JS regex or string literal added to this file that contains a backslash escape (`\d`, `\w`, `\b`, `\t`, `\n`, `\s`, `\r`, etc.) must be written double-backslashed in the Python source (e.g. `\\d`) to survive Python's string-literal parsing intact. There is no reliable warning for escapes that happen to coincide with valid Python escapes (`\t`, `\n`, `\r`) — those fail completely silently at the Python level and only surface as broken JS in the browser.

### 15.10 Docker volume mounts (`docker-compose.yml`)

Added bind mounts for `cache.db`, `checkpoints.db`, `domains.db`, `scheduled_runs.db`, `costs.db` (mirroring the pre-existing `drafts.db` mount). Known, accepted, pre-existing caveat (not a new regression from this change): all six stores use `PRAGMA journal_mode=WAL`, so a single-file bind mount doesn't carry the `-wal`/`-shm` sidecar files across container recreation — this already applied to `drafts.db`'s mount before this session; extending the same pattern to the other five is consistent, not a new gap. A more robust fix (mounting the whole app directory, or a dedicated `/data` subdirectory, as a volume) would change the existing convention and was judged out of scope for this pass.

### 15.11 Explicitly not built this session

- **LinkedIn API direct-publish**: the user was asked directly (via a clarifying question) whether they already have a LinkedIn Developer App / OAuth credentials; they chose "skip for now." Nothing was built — no OAuth flow, no token storage, no publish-API call. The existing `openLinkedIn()` compose-window flow in `ui.py` is unchanged and remains the only publish path. This is a live, deferred decision, not a permanent no — don't build it without asking again, and note that even with credentials, it requires a user-driven browser OAuth consent step that an agent cannot complete unattended.
- **Phase 5** (persistent user style profile/feedback loop, multi-format output beyond LinkedIn posts, topic/source performance dashboard): not started, not even designed — the plan doc (`FEATURE_BACKLOG_PLAN.md`) only has one line per item, explicitly flagged there as needing its own design pass before implementation.

## 16) Feature Backlog Phase 5 (session `c3152374`, 2026-08-29) — implemented, uncommitted

Three items, all built in explain-then-approve mode (user approved each design before building). All live-verified, all tested. Nothing committed. Test suite: 50 tests total (was 32 after Phases 1-4, +18 across the three Phase 5 items).

### 16.1 Persistent user style profile (`style_profile.py`)

New SQLite store (`style_profile.db`, WAL, same pattern as `domain_store.py`/`cost_tracker.py` — 7th store). `style_rules` table: `id`, `persona` (`*` = all personas, or a specific persona id), `rule_text` (plain text), `source` (`manual` or `feedback`), `created_at`, `applied_count`, `disabled` (soft-delete).

Functions: `add_rule`, `list_rules(include_disabled)`, `get_active_rules(persona)` (filters by persona match + not disabled), `update_rule`, `set_disabled`, `delete_rule`, `get_rule`, `increment_applied([ids])`, `active_rules_block(persona)` → formatted `Standing style rules:\n- rule1\n- rule2` string (or `None` if no active rules).

`graph.py` changes: `import style_profile`. `_build_author_prompt` gained a `style_rules_block: Optional[str]` param, injected between `CONSTRAINTS:` and `EXAMPLE OUTPUT:` sections. `author_node` fetches `style_profile.get_active_rules(persona)`, passes `active_rules_block(persona)` to the prompt, and calls `increment_applied([ids])` after the draft is generated + snapshotted to version history.

`ui.py` changes: `/api/refine` injects the same block (reads persona off thread state, like it does for the writer provider) and increments applied count after refining. New endpoints: `GET /api/style-rules` (list, optional `include_disabled`), `POST /api/style-rules` (create, 400 on empty rule_text), `PATCH /api/style-rules/{id}` (update text/persona), `POST /api/style-rules/{id}/toggle` (enable/disable), `DELETE /api/style-rules/{id}` (404 if missing). New Style Rules `<details>` panel in the Start Flow card: add-rule input + persona-scoped dropdown + Add button, rule list with toggle/delete per row. Promote-on-refine: after any Quick Refine succeeds, `offerPromoteRefine(instruction)` shows a 6-second toast with a "Save" button — one-click saves the instruction as a `source="feedback"` rule under the current persona; not auto-saved, auto-dismisses. `loadStyleRules()` called on page init.

Tests: `tests/test_style_profile.py` — 7 tests (add/list, empty rejection, persona+disabled filtering, applied-count increment, empty-block, block formatting, delete).

**Design decisions**: explicit-curation only — no automatic quality scoring, no LLM-judge, no thumbs-up/down (the plan doc warned this needed a signal strategy; the chosen strategy is deliberate user curation only, because automatic scoring is a rabbit hole with weak payoff for a single-user tool). Rules are advisory (injected into the prompt; the LLM may deviate — this isn't enforcement). The 3 base personas stay hard-coded; rules layer on top as adjustable preferences.

### 16.2 Multi-format output (`FORMATS` dict in `graph.py`)

New `FORMATS: Dict[str, Dict[str, str]]` module-level dict (defined right after `PERSONAS`), with `DEFAULT_FORMAT = "post"`. Three formats:
- `post` — Single Post. `structure`/`example` left empty → falls back to the persona's own structure/example (preserves byte-identical output to the pre-format prompt; verified by regression test). `constraints` = the existing "Under 220 words. No emojis. Max 3 hashtags. No em dashes."
- `thread` — Thread (5-7 posts). Own `intro`/`structure`/`example`/`constraints`: numbered posts, each ≤280 chars, hook + body + CTA post.
- `carousel` — Carousel (6-8 slides). Own `intro`/`structure`/`example`/constraints: `[Slide N] Title / lines` format, each line ≤~60 chars.

`video_script` was in the proposed design but the user explicitly dropped it ("No need for a video script for now") — not built, not in the dict.

`_build_author_prompt` refactored: now takes `fmt: str = "post"` as a 5th param. Persona contributes `intro` + `voice` (style layer); format contributes `structure` + `example` + `constraints` (shape layer). For `post`: `structure = fconfig["structure"] or pconfig["structure"]` (persona's structure wins when format's is empty). For `thread`/`carousel`: format's structure/example/constraints override the persona's. The prompt's opening line now appends the format's `intro` after the persona's intro.

`AgentState` gained `format: str`. `author_node` reads `state.get("format") or DEFAULT_FORMAT` and passes it through.

`ui.py`: `StartRequest.format` (default `"post"`), `format-select` dropdown in the Start Flow card (next to persona-select), `_state_snapshot` returns `format`, `startFlow()` payload includes it, `/api/refine` reads format off thread state and adds a format hint to the refine prompt, `WebhookStartRequest.format`, `/api/webhook/trigger` threads it through.

`scheduler.py`: `run_scout_analyst_job` gained `fmt: str = "post"` param (passed into the graph's initial state). `_run_scheduled_job` reads `SCHEDULER_FORMAT` env var (default `"post"`).

`.env.example`: `SCHEDULER_FORMAT=post`, `WEBHOOK_FORMAT=`.

Tests: `tests/test_formats.py` — 5 tests (post = byte-identical legacy regression, each non-post format injects own structure/example/constraints, format+persona compose with no persona-structure leak, unrecognized format falls back to post, style rules layer on top of format).

**Design decisions**: format is orthogonal to persona (persona = voice, format = shape — they compose, one of each per run). `post` is byte-identical to legacy (no regression for existing runs). No carousel image generation / slide rendering — text only. No auto-thread-splitting UI (a thread is already numbered in the text; the user pastes it into LinkedIn's composer and splits manually). `openLinkedIn()` unchanged.

### 16.3 Performance dashboard (`dashboard.py`, Tier 1)

New read-only aggregation module. No new SQLite store, no schema changes to existing stores. Queries the 4 existing stores (`cost_tracker`, `draft_store`, `scheduled_store`, `style_profile`) and returns structured dicts.

Functions:
- `cost_summary(days)` — queries `cost_tracker.get_daily_cost(days)`, aggregates by day/node/provider. Returns total cost, call count, daily breakdown, by_node, by_provider.
- `run_summary(days)` — queries `scheduled_store.list_all_runs(500)`, filters by date, groups by week. Returns total runs, emailed/reviewed counts + rates, avg candidates/run, weekly rollup, by_topic distribution.
- `draft_summary(days)` — queries `draft_store.get_all_drafts(500)` + `get_all_versions(1000)`, filters by date. Returns total published, avg draft length, version counts (refine/author/manual_edit), weekly published count.
- `topic_distribution(days)` — derived from `run_summary`'s by_topic.
- `style_rule_usage()` — queries `style_profile.list_rules(include_disabled=True)`. Returns total/active/disabled counts + rules sorted by applied_count desc.
- `build_dashboard(days)` — calls all 5 and returns one dict.

Supporting additions to existing stores (new read functions, not schema changes):
- `cost_tracker.get_daily_cost(days)` — daily rollup grouped by node + provider.
- `draft_store.get_all_drafts(limit)` + `get_all_versions(limit)` — cross-thread reads (data already existed, just no query for it).
- `scheduled_store.list_all_runs(limit)` — returns runs with candidate counts (parses the existing `candidates_json`).

`ui.py`: `GET /api/dashboard?days=30` (clamped 1-365). New collapsible "Performance Dashboard" `<details>` panel before the Scout Debug section. Five sections rendered as plain HTML tables and CSS pills (no charting library): Cost (by node/provider tables), Runs (weekly rollup with email/review rates), Drafts (published count, avg length, refine/author/manual counts), Topic Distribution (pills), Style Rules (rule table with applied counts + disabled state). On-demand "Refresh Dashboard" button + days input — no auto-polling (data changes slowly).

Tests: `tests/test_dashboard.py` — 6 tests (cost aggregation, run rates + topic distribution, draft/version counts, style rule usage, full build_dashboard all-sections, empty-data no-crash). Seeds all 4 stores with temp DBs in setUp.

**Tier 2 (deferred per user)**: source-level attribution — "which domain produces the most published drafts / highest-scored articles." Requires adding `source_domain` to `draft_store`'s `published_drafts` table (populated at publish time from the article's `source` field in checkpoint state), and optionally extracting per-article analyst scores to a queryable table instead of the candidates JSON blob. This is a schema change to a store already in production (`drafts.db`), and only pays off after enough future runs populate the new column. Not built; documented as a follow-up.

### 16.4 What was NOT built this session

- **`video_script` format**: proposed in the Item 2 design, user explicitly dropped it ("No need for a video script for now"). Not in the `FORMATS` dict. Can be added later — same plumbing, just a new dict entry + dropdown option.
- **Dashboard Tier 2 (source attribution)**: deferred per user. See §16.3 above.
- **LinkedIn API direct-publish**: not revisited this session. Still deferred from the prior session (user chose "skip for now"). Don't build without asking again.
- **Nothing committed**: per the standing user preference, commits only happen when the user explicitly says "commit and push." That instruction didn't come this session. All Phase 5 work is in the working tree alongside the uncommitted Phases 1-4.

---

## 17) Writer Prompt Improvements + Hybrid Paywall Exclusion — session `6830d7a1` (2026-08-30)

**Status: PAUSED / resume pending.** All work in this section is committed and merged to `main` (PRs #1 and #2). Two files remain uncommitted: `docker-compose.yml` (added volume mount for `writer_examples.txt`) and `writer_examples.txt` (user's personal content). See `sessions/2026-08-30_6830d7a1.md` for the full session log.

### 17.1 UI fixes (PR #1, commit `9a69ff1`)

- **Thread ID drop-down**: `cost_tracker.list_recent_threads(limit=10)` queries distinct `thread_id` from `usage` table ordered by most recent `ts`. `GET /api/threads` endpoint returns the list. UI input changed to `<input list="thread-options">` with a `<datalist>` populated on page load. Users can still type a new thread ID.
- **Model default**: `graph.py` `_get_chat_model` ollama fallback changed from `os.getenv("OLLAMA_MODEL", "llama3.1")` to `os.getenv("OLLAMA_MODEL", "deepseek-v4-flash:cloud")`. UI placeholders updated to match.
- **Live State JSON collapsed**: `<section class="card"><h2>Live State JSON</h2><pre>` changed to `<details><summary>...</summary><pre>` pattern, matching 3b Raw Articles. Collapsed by default.

### 17.2 Hybrid paywall exclusion (PR #1, commit `9a69ff1`)

Three env-configurable layers, all in `settings.py`:

1. **`PAYWALLED_DOMAINS`** (default: `theinformation.com,thelogic.co`) — small domain blocklist for genuinely all-paywall, non-technical outlets. Checked in `_normalize_rss_entries` via `_extract_domain(url)` against the set. Drop reason: `paywall_domain`. Never includes mixed platforms like medium.com or substack.com (user explicitly rejected that approach).
2. **`PAYWALL_MARKERS`** (default: 16 phrases) — substrings scanned case-insensitively against `title + "\n" + summary` at RSS level (no fetch). Catches paid Substack/Medium posts and paywall teasers. Drop reason: `paywall_marker`.
3. **`PAYWALL_PROBE`** (default: `false`, opt-in) + `PAYWALL_PROBE_MAX` (default: `40`) — after dedup+sort, fetches up to N article bodies in parallel via `_http_fetch_text()` (reusing `_is_public_http_url` guard). Detects paywalls via HTTP 401/403, body markers (same `PAYWALL_MARKERS` list), and `<title>` hints. Fail-open: any fetch error keeps the article. Drop reason logged in `scout_debug.stats.paywall_dropped`.

All three drop reasons surface in the scout audit and source stats. `scout_debug.stats` includes `paywall_blocked_domains`, `paywall_markers`, `paywall_probe_enabled`, `paywall_probe_max`, `paywall_probed`, `paywall_dropped`.

Evolution: iteration 1 was domain-blocklist-only (too blunt, blocked medium/substack); iteration 2 was fetch-probe-only (user wanted to re-evaluate); iteration 3 is the hybrid (user chose after seeing all options). The fetch probe defaults OFF to keep the synchronous scout fast.

New functions: `_http_fetch_text(url, timeout, max_bytes)`, `_is_paywalled_article(url) -> (bool, str)` in `graph.py`. New settings: `get_paywalled_domains()`, `get_paywall_markers()`, `get_paywall_probe_enabled()`, `get_paywall_probe_max()` in `settings.py`.

### 17.3 Writer prompt improvements (PR #2, commit `6830d7a`)

#### A — Rewritten prompt structure (`graph.py:1399`)
`_build_author_prompt` signature now accepts `article_body: str = ""`. The prompt:
- Collapsed labelled `VOICE:` / `STRUCTURE:` / `CONSTRAINTS:` / `EXAMPLE OUTPUT:` blocks into a flowing brief: persona intro + voice + structure + hard constraints.
- Removed the synthetic persona `example` from the prompt (old code injected `pconfig["example"]` or `fconfig["example"]`). The examples made every post pattern-match one synthetic post's vocabulary/rhythm.
- Added `_ANTI_AI_TELLS` constant (graph.py:86) — hard constraints banning: "delve", "navigate", "landscape", "realm", "tapestry", "robust", "seamless", "leverage", "synergy", "transformative", "game-changer", "paradigm shift", "it's important to note", "the promise is X, the reality is Y", em dashes, semicolons, symmetric bullet lists, hedging ("arguably", "perhaps"), greeting openers, neat summary sentence endings.
- Also injected into the refine prompt in `ui.py` (imported `_ANTI_AI_TELLS` from graph).

#### B — Few-shot from user's writing (`settings.py:135`, `graph.py:1431`)
- `WRITER_EXAMPLES` env var: inline text (examples separated by `\n---\n`) or `file:/path/to/file`.
- `get_writer_examples()` in `settings.py` reads the env var, handles `file:` prefix, splits on `\n---\n`.
- In `_build_author_prompt`, if examples exist, injects: "Here are examples of how I actually write. Match this voice and rhythm, not a generic AI tone:" followed by the examples joined by `\n\n---\n\n`.
- Empty by default. User configured `file:/app/writer_examples.txt` with 4 real posts, bind-mounted in Docker.

#### C — Free-text feedback on draft (`ui.py:599`)
- Added `<textarea id="custom-refine">` + "Refine with Feedback" button next to Quick Refine buttons.
- Wires through existing `refineDraft(instruction)` → `POST /api/refine` (same path as the 5 fixed buttons). Anti-AI-tells applied via the refine prompt.

#### D — Learn from manual edits (`ui.py:2228`, `ui.py:1464`)
- `POST /api/learn-from-edit` (`LearnFromEditRequest`): takes `thread_id` + `published_draft`. Fetches last "author" version from `draft_store.get_versions()`, compares against published draft. If different, uses writer LLM to extract one concise style rule from the diff. Returns `{proposed_rule, changed}`.
- UI: `approveDraft()` calls the endpoint after publish. If `changed` and `proposed_rule` exist, shows a toast offering one-click save as a style rule (source: `learned_from_edit`).
- `offerLearnFromEdit(ruleText)` — toast with "Save rule" button, auto-dismisses after 12s.

#### E — Article body fetch (`graph.py:1497`)
- `author_node` now fetches the full article body before building the prompt:
  ```python
  if article_url and _is_public_http_url(article_url):
      article_body = _http_fetch_text(article_url, timeout=10.0, max_bytes=200_000)
  ```
- Fail-open: on any exception, `article_body = ""`, prompt falls back to RSS summary.
- `_build_author_prompt` uses `article_body` if available (label "Article body:"), otherwise falls back to `summary` (label "Article summary:"). Body truncated to `AUTHOR_BODY_MAX_CHARS = 8000`.

### 17.4 Tests

- Total: 63 tests, all passing.
- New tests: 8 paywall tests (domain blocklist, marker detection, fetch probe — HTTP 403, body marker, free article, fetch error fail-open, non-public URL), 5 author prompt tests (anti-AI-tells present, article body used when provided, summary fallback, WRITER_EXAMPLES injection, examples omitted when empty).
- Updated: `test_formats.py` — removed byte-identical-to-legacy test (prompt structure changed); updated format tests to check structure/constraints without the old `example` field.

### 17.5 What's uncommitted

```
 M docker-compose.yml          — added: ./writer_examples.txt:/app/writer_examples.txt
?? writer_examples.txt         — user's 4 real LinkedIn posts (personal content)
```
`docker-compose.yml` is safe to commit independently. `writer_examples.txt` contains personal writing — commit only with explicit user approval.

### 17.6 Dead ends (don't retry)

- Don't add medium.com or substack.com to `PAYWALLED_DOMAINS` — user explicitly rejected.
- Don't put multi-line `WRITER_EXAMPLES` in `.env` for Docker — docker compose's parser fails with `key cannot contain a space`. Use `file:` prefix.
- Don't re-inject synthetic persona examples into the writer prompt — they made output sound MORE AI.
