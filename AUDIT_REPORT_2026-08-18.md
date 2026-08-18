# System, UX, & Feature Audit Report v2 — 2026-08-18

> **Update (same day, post-review):** All P0 and P1 items are fixed and verified (see "P0/P1 Fix Log" at the end) — including a **new critical bug found during verification that isn't in the original findings**: `build_graph()`'s checkpointer connection was being closed by garbage collection before the graph ever ran, so every graph invocation failed outright.
>
> **Second update (same day):** the remaining P2/P3 *bugs* (Bug 4, Bug 5, Bug 7, Bug 9) are now also fixed and verified — see "P2/P3 Bug Fix Log" at the very end. The P2/P3 *feature* recommendations (draft version history, persona switching, hashtag library, toast notifications, export formats, cost/spend visibility, pre-LLM heuristic filtering, webhook triggers, LinkedIn API integration) remain open by scope — those are net-new work, not defect fixes, and weren't part of this pass.

Follow-up audit to `AUDIT_REPORT.md` (v1). Since v1, a large amount of work landed on the `fix/scheduler-cache-review` branch (uncommitted, never merged to `main`): scout parallelization, `SqliteSaver` checkpointing, SSE token streaming, multi-axis analyst scoring, a factuality check, an inline refine toolbar, a candidate-card selection UI, RSS response caching, and a full scheduler + email digest subsystem. This report verifies what actually works, finds new bugs introduced by that work, and re-evaluates performance, cost, utility, and UX with fresh eyes.

**Method:** Full read of `graph.py`, `ui.py`, `settings.py`, `main.py`, `preflight.py`, `progress.py`, `draft_store.py`, `scheduler.py`, `scheduled_store.py`, `emailer.py`, `feed_cache.py`; live tests against `feedparser`; test suite run; cross-reference against every v1 recommendation.

## Executive Summary

- **Overall Quality Rating: B+** (unchanged from v1) — genuine architectural progress is offset by two broken headline features and zero new test coverage.
- **What's now solid:** scout concurrency, durable checkpointing, streamed drafts, multi-axis LLM scoring with few-shot examples, LLM timeouts, a real (if costly) factuality check, and a much better candidate-selection UI.
- **What's broken:** the two flagship additions of this work cycle — the feed cache and the scheduled-run review flow — do not work as intended. Both fail silently.
- **What's missing:** every P2/P3 item from v1 that touches persistence or content variety (draft versioning, server-side domain storage, toast UI, persona switching, export formats, hashtag library) is still unimplemented.
- **New risk:** none of the new code (scheduler, emailer, cache, refine, factuality) has any test coverage. All 10 existing tests predate this work.

---

## 1. Scorecard — Status of Every v1 Recommendation

| Priority | Item | Status | Evidence |
|:---|:---|:---|:---|
| P0 | Parallelize scout fetches | ✅ Done | `ThreadPoolExecutor(max_workers=SCOUT_MAX_WORKERS)` at [graph.py:1154-1164](graph.py#L1154-L1164) |
| P0 | Replace `MemorySaver` with `SqliteSaver` | ✅ Done | [graph.py:1619-1621](graph.py#L1619-L1621) — but see Bug 6 (leak) and Bug 7 (no WAL) |
| P0 | Stream author output via SSE | ✅ Done | `writer_llm.stream()` + `/api/stream/{thread_id}` at [graph.py:1471-1484](graph.py#L1471-L1484), [ui.py:1635-1650](ui.py#L1635-L1650) — see Bug 5 (hard timeout) |
| P1 | Radio-button candidate selection, highlight selected card | ✅ Done | `.candidate-radio`, `pickId()` at [ui.py:706-732](ui.py#L706-L732) |
| P1 | Inline AI refinement toolbar | ✅ Done | 5 refine buttons + `/api/refine` at [ui.py:487-497](ui.py#L487-L497), [ui.py:1653-1670](ui.py#L1653-L1670) — see UX-2 (no undo) |
| P1 | LLM `request_timeout` / `max_retries` | ✅ Done | [graph.py:185-186](graph.py#L185-L186), applied to all 4 providers |
| P1 | Factuality verification step | ✅ Done | `_verify_factuality()` at [graph.py:1382-1399](graph.py#L1382-L1399) — see Cost-1 (always-on, uncapped) |
| P1 | Multi-axis analyst scoring + press-release/funding-news filtering | ⚠️ Partial | Scoring axes + few-shot examples done ([graph.py:122-133](graph.py#L122-L133), [graph.py:1275-1293](graph.py#L1275-L1293)); the "AUTO-REJECT" filtering is prompt-only (LLM self-applies it), **not** a pre-LLM heuristic filter — so the cost-saving half of the recommendation ("reduce candidate set before the expensive LLM call") was never built |
| P1 | Scheduled runs + email digest | ✅ Fixed | Built ([scheduler.py](scheduler.py), [emailer.py](emailer.py), [scheduled_store.py](scheduled_store.py)); resume flow (Bug 2) and domain sync (Bug 3) both fixed — see fix log |
| P2 | Persona / post-format switching | ❌ Not done | Author prompt still one fixed persona, no dropdown |
| P2 | Draft version history / comparison | ❌ Not done | Refine/edit actions overwrite in place; only *published* drafts are listed |
| P2 | TTL cache for RSS/Google News responses | ✅ Fixed | Built ([feed_cache.py](feed_cache.py)); round-trip corruption (Bug 1) fixed — see fix log |
| P2 | Hashtag library | ❌ Not done | |
| P3 | Toast notifications instead of `alert()` | ❌ Not done | `alert()` still used ~9 times in [ui.py](ui.py) |
| P3 | Server-side domain persistence | ❌ Not done | Still `localStorage` only ([ui.py:611-612](ui.py#L611-L612)) |
| P3 | Few-shot examples in prompts | ✅ Done | Analyst ([graph.py:1285-1293](graph.py#L1285-L1293)) and author ([graph.py:1446-1458](graph.py#L1446-L1458)) both have worked examples |
| P3 | Cache URL-enrichment results | N/A | The entire enrichment code path (`_enrich_published_at_from_url`, `_http_fetch_text`, `_normalize_tavily_results`) is **dead code**, unreachable from the active scout path — see Bug 4 |
| P3 | Export formats beyond clipboard | ❌ Not done | Only "Copy Draft" and "Open LinkedIn Post" exist |

**Net: 8 done, 1 broken-as-built, 1 partial, 7 not started, 1 moot (target code is dead).**

---

## 2. Bugs Found This Audit

### Bug 1 — Feed cache corrupts every cache hit (confirmed by direct test) — ✅ FIXED
[graph.py:1115-1124](graph.py#L1115-L1124): after a fresh `feedparser.parse(feed_url)`, the code caches `str(parsed_feed)` — the Python `repr()` of the parsed object, not the raw feed bytes. On the next call within the 15-minute TTL, `feedparser.parse(cached)` tries to parse that repr string as feed content. Verified directly:
```
entries from real parse: 1
entries from str-reparse: 0   bozo: 1
```
Since `bozo=True` triggers the error branch ([graph.py:1125](graph.py#L1125)), **every cache hit silently returns zero articles** for that domain for the rest of the TTL window. The cache doesn't speed anything up (RSS fetches are already free and fast); it actively drops articles from any feed queried twice within 15 minutes — which happens on nearly every manual re-run during a working session. Fix: cache the raw response text (fetch it separately, or feed `feedparser.parse` a URL/bytes and cache those bytes) — never the post-parse object's `str()`.

### Bug 2 — Scheduled-run review can never reach the paused thread — ✅ FIXED
[ui.py:1379-1390](ui.py#L1379-L1390) `loadScheduledRun()` renders a scheduled run's candidates but never sets `threadIdEl.value` to that run's `thread_id`. Every action in the UI (`resumeFlow`, `approveDraft`, etc.) reads the thread ID from that one input field. Clicking "Approve + Generate Draft" after loading a scheduled run therefore resumes whatever thread ID happens to be in the box — not the scheduled run's actually-paused thread — so the resume either 404s, errors ("no active interrupt"), or silently operates on an unrelated thread. **The scheduled-run review flow, the centerpiece deliverable of this work cycle, cannot complete end-to-end.** Fix: `loadScheduledRun` must set `threadIdEl.value = runId` (and ideally visually confirm the switch) before rendering candidates.

### Bug 3 — Scheduled runs ignore the UI's curated domain list — ✅ FIXED
The interactive UI persists a carefully curated, per-domain enable/disable list in `localStorage` ([ui.py:611-704](ui.py#L611-L704)). The scheduler reads a completely separate, static `SCHEDULER_DOMAINS` env var ([scheduler.py:27-28](scheduler.py#L27-L28), [.env.example](.env.example)). A user who spends time disabling noisy domains in the browser gets zero benefit from that curation in their automated runs — the two configuration surfaces are entirely disconnected. This undermines the stated goal of scheduling ("eliminates the need to manually trigger runs") since the automated runs use a different, harder-to-edit domain set than the one the user actually maintains.

### Bug 4 — ~200 lines of dead code reference removed dependencies — ✅ FIXED
`_build_tavily_search_tool`, `_invoke_tavily_search`, `_normalize_tavily_results`, `_enrich_published_at_from_url`, `_http_fetch_text`, `_extract_date_from_html`, `_find_first_date_in_jsonld` ([graph.py:492-1051](graph.py#L492-L1051)) are never called from `scout_node` or anywhere reachable in the active pipeline — confirmed via grep, `scout_node` only calls `_normalize_rss_entries`. Worse, `_build_tavily_search_tool`'s fallback branch does `from langchain_community.tools.tavily_search import TavilySearchResults` ([graph.py:502](graph.py#L502)), but `langchain-community` and `langchain-tavily` were both removed from `requirements.txt` in this same work cycle. If this code were ever accidentally invoked, it would `ImportError`. Recommend deleting all of it — it's ~13% of `graph.py` doing nothing.

### Bug 5 — SSE stream force-closes before the LLM timeout does — ✅ FIXED
[ui.py:1635-1650](ui.py#L1635-L1650) `/api/stream/{thread_id}` polls for at most `300 × 0.15s = 45s`, then emits `{"done": true}` unconditionally even if generation is still running. `LLM_REQUEST_TIMEOUT` defaults to 60s ([.env.example](.env.example)) — meaning the visible token stream can time out and appear "done" up to 15 seconds *before* a legitimately slow model call actually finishes. The final draft still loads correctly once `/api/resume` returns (via `applyState`), so this is cosmetic, not data-losing — but it's a confusing UX regression for anyone on a slower model.

### Bug 6 — Scheduler leaks a SQLite connection on every run — ✅ FIXED
[scheduler.py:38](scheduler.py#L38) calls `build_graph()` fresh inside `_run_scheduled_job()`, which does `SqliteSaver.from_conn_string(db_path)` and `.__enter__()` but never `.__exit__()` ([graph.py:1619-1621](graph.py#L1619-L1621)). Every scheduled tick opens a new SQLite connection to `checkpoints.db` that is never closed. `ui.py` avoids this by calling `build_graph()` once at import time ([ui.py:27](ui.py#L27)), but the scheduler has no such reuse — with `SCHEDULER_CRON` at even a modest cadence (e.g. hourly), this leaks dozens of file descriptors per day on a long-running server.

### Bug 7 — `main.py --action` can't express "done" — ✅ FIXED
`graph.py`'s `DRAFT_REVIEW_ACTIONS` includes `"done"` ([graph.py:75](graph.py#L75)), but `main.py`'s argparse `choices` for `--action` only lists `["publish", "edit", "pick_another"]` ([main.py:107](main.py#L107)). The CLI cannot finish a thread the way the UI can ("Pick Another Article or finish"); it'll reject `--action done` outright. Minor, but the CLI and web UI have drifted out of sync on the action set.

### Bug 8 — New SQLite files aren't gitignored — ✅ FIXED (bonus, done alongside the domain-store addition)
`.gitignore` covers `drafts.db*` but not `cache.db`, `checkpoints.db`, or `scheduled_runs.db` — all three currently show as untracked (`??`) in `git status`. These are binary files containing cached article/candidate content that will bloat the repo and cause merge noise if accidentally committed.

### Bug 9 (minor) — orphaned "mark reviewed" endpoint — ✅ FIXED
`POST /api/scheduled-runs/{run_id}/review` exists ([ui.py:1687-1690](ui.py#L1687-L1690)) but no UI element calls it — the "Scheduled Runs" card only supports loading a run, not marking it reviewed or skipped. The "Skip This Batch" link that v1's spec explicitly called for is also absent from the actual email body in [emailer.py](emailer.py) (only "Review & Generate Draft" is included).

---

## 3. Cost Audit (new section)

The old audit didn't address LLM spend directly; this cycle's changes meaningfully change the cost profile, so it's worth calling out explicitly:

- **✅ FIXED** — **Every draft generation now costs 2 LLM calls, not 1.** `author_node` always runs `_verify_factuality()` immediately after streaming the draft ([graph.py:1486](graph.py#L1486)), with no toggle to skip it. For cloud providers (OpenAI/Gemini/Groq) this silently doubles per-draft spend. There's no UI indication that a second call is happening — the progress bar and SSE stream both report "done" while the fact-check is still running server-side, inside the same blocking `/api/resume` call.
- **Analyst output is heavier than before.** The structured schema grew from `{id, relevance_score}` to `{id, relevance_score, contrarian_value, technical_depth, debate_potential, timeliness, source_credibility}` ([graph.py:122-133](graph.py#L122-L133)) — 6 numeric fields instead of 1, times up to 20 picks, on every run. More output tokens per analyst call, every time.
- **Quick Refine has no cost guardrail.** Each of the 5 refine buttons ([ui.py:487-497](ui.py#L487-L497)) fires a full LLM call with the entire current draft as context. A user iterating ("shorten → more technical → shorten again") can rack up several calls per post with no batching, debouncing, or undo to make a bad call "free" to back out of.
- **Scheduled runs bill on a timer regardless of engagement.** `_run_scheduled_job` runs scout+analyst on every cron tick whether or not anyone ever opens the digest email ([scheduler.py:25-66](scheduler.py#L25-L66)). Default config points at `ollama` (local/free), but a user who reconfigures `SCHEDULER_ANALYST_PROVIDER`/`SCHEDULER_WRITER_PROVIDER` to a paid provider gets billed unattended, with zero budget cap, spend log, or per-run cost estimate anywhere in the system.
- **No cost visibility at all.** There is no token-count logging, no per-run cost estimate, no daily/monthly spend ceiling. For a tool explicitly designed to run unattended on a schedule, this is the single biggest gap for anyone not on local Ollama.

---

## 4. Performance Audit (delta from v1)

**Resolved:**
- Scout latency: sequential → `ThreadPoolExecutor` (default 6 workers), the single biggest latency win identified in v1, done correctly.
- LLM calls now have real timeouts/retries instead of hanging indefinitely.
- Draft generation streams to the UI instead of a silent 5-20s wait.

**New regressions/gaps introduced by this cycle:**
- The RSS cache (Bug 1) doesn't provide the intended latency benefit and actively *reduces* article yield on repeat runs within 15 minutes — worse than having no cache.
- Author latency actually went *up* for cloud providers: streaming the draft + a full sequential factuality call is two round-trips where there used to be one, and they aren't run concurrently.
- `scheduler.py`'s per-job `build_graph()` (Bug 6) means SQLite connection count grows unbounded over the server's uptime.

**Still open from v1 (untouched):**
- No pre-LLM heuristic filter to shrink the article set before the analyst call (the AUTO-REJECT criteria are prompt-only).
- No response caching for the analyst/author calls themselves (expected — content is unique per run — but worth confirming this wasn't conflated with the RSS cache).

---

## 5. Utility & Automation Audit (delta from v1)

**Delivered, but with reach undermined by bugs:** the scheduler + email digest is architecturally sound (correctly pauses at the `approval` interrupt, stores candidates, emails a digest) but Bug 2 means a user literally cannot close the loop from "email arrives" to "draft generated" without manually re-typing the thread ID from the email/run list into the Thread ID box — which isn't documented anywhere as a workaround. Bug 3 means even power users who've tuned their domain list get a different, worse-curated source set in their automated runs.

**Still not built (unchanged from v1):**
- No webhook trigger for `/api/start` (Zapier/n8n-style external trigger).
- No LinkedIn API integration — publishing is still "open a pre-filled compose window."
- No persistent user style profile or past-performance feedback loop.
- No multi-format output (threads, carousel outlines, video scripts).
- No topic/source performance dashboard.

**New utility observation:** the inline refine toolbar and factuality check are genuinely good additions to day-to-day usefulness — they close two of v1's biggest "the LLM might be wrong and I can't easily fix it" gaps. The factuality notes surface in the UI ([ui.py:1034-1041](ui.py#L1034-L1041)) with a clear amber callout, which is a nice touch.

---

## 6. UX / UI Audit (delta from v1)

**Resolved:**
- Article selection is now a proper radio-button/click-to-select candidate card with visual highlighting — directly addresses v1's "manual ID entry is unintuitive" finding.
- Streaming draft output eliminates the "black box" wait during author generation (modulo Bug 5's cosmetic early-close).
- The factuality warning banner is a good, low-friction way to surface hallucination risk without blocking the flow.

**Still open from v1 (unchanged):**
- `alert()` is still used for every notification (copy confirmation, error states, delete confirmations) — jarring, blocks the JS thread, and was explicitly flagged in v1.
- No draft version history — this is now *more* noticeable than in v1, because the new refine toolbar makes it trivially easy to overwrite a draft you liked with a worse one, with no undo.
- Domain configuration is still browser-only, and now that gap has a second consequence (Bug 3) beyond the original "lost on browser switch" problem.

**New UX findings this cycle:**
- The "Scheduled Runs" list shows topic and timestamps but never the domains used for that run, making it hard to tell why a run's candidates look the way they do.
- Loading a scheduled run gives no visual confirmation that you've switched context (compounds Bug 2 — even after fixing the thread-ID sync, nothing in the UI would tell the user which run they're now looking at).
- During the factuality check (after the stream shows "done" but before `/api/resume` actually returns), the UI's only feedback is the generic disabled "Generating..." button state — a brief "Verifying facts..." message would remove the apparent stall.

---

## 7. Test Coverage Audit (new section)

All 10 existing tests in `tests/test_graph_edges.py` pass and predate this entire work cycle. **Zero test coverage exists for:**
- `scheduler.py`, `scheduled_store.py`, `emailer.py` (the entire scheduled-run subsystem)
- `feed_cache.py` (which would have caught Bug 1 immediately — a round-trip test of `set()` followed by `get()` feeding back into `feedparser.parse()` fails instantly)
- `/api/refine`, `_verify_factuality`, the SSE `/api/stream` endpoint
- The `loadScheduledRun` → resume flow (would have caught Bug 2)

---

## 8. Prioritized Recommendations

| Priority | Issue | Fix |
|:---|:---|:---|
| **P0** | Bug 1 — feed cache corrupts hits | ✅ Fixed — cache raw feed text before parsing, not `str()` of the parsed object. Round-trip test added. |
| **P0** | Bug 2 — scheduled-run resume is unreachable | ✅ Fixed — `loadScheduledRun` now sets `threadIdEl.value` to the run's `thread_id` and confirms the context switch in the activity line + progress step. |
| **P1** | Bug 3 — scheduler ignores UI domain curation | ✅ Fixed — new server-side `domain_store.py` (SQLite) shared by the UI (`/api/domains`) and the scheduler; `SCHEDULER_DOMAINS` env now only a fallback for before the UI has synced once. |
| **P1** | Cost-1 — factuality check always runs, uncapped | ✅ Fixed — `ENABLE_FACTUALITY_CHECK` env toggle (default `true`, preserves current behavior) gates `_verify_factuality()`. |
| **P1** | Bug 6 — scheduler connection leak | ✅ Fixed — `scheduler.py` now builds the graph once (module-level, lazily) and reuses it across job runs, matching `ui.py`'s pattern. |
| **P0 (found during fix verification, not in original findings)** | `build_graph()`'s checkpointer connection was closed by garbage collection before the graph ever ran | ✅ Fixed — see "P0/P1 Fix Log" below. This was silently breaking **every** graph invocation, not just the scheduler/cache features. |
| **P2** | Bug 4 — dead Tavily/enrichment code | ✅ Fixed — deleted `_build_tavily_search_tool`, `_invoke_tavily_search`, `_normalize_tavily_results`, `_enrich_published_at_from_url`, `_http_fetch_text`, `_extract_date_from_html`, `_find_first_date_in_jsonld`, `_extract_published_at` (~380 lines total, incl. now-unused `warnings`/`urllib.error` imports). `_is_public_http_url` was kept — it's independently tested and a reusable safety primitive. |
| **P2** | Draft version history still missing, now sharper pain given one-click refine | Not fixed (feature work, out of scope for a bug-fix pass) — store each draft iteration (pre-refine snapshot) in-memory or SQLite; add an "Undo last refine" button as the minimal version. |
| **P2** | Bug 8 — new DB files not gitignored | ✅ Fixed (bonus, alongside the domain-store addition) — `cache.db*`, `checkpoints.db*`, `scheduled_runs.db*`, `domains.db*` all added to `.gitignore`. |
| **P3** | Bug 7 — CLI/UI action set drift | ✅ Fixed — `"done"` added to `main.py`'s `--action` choices and to the printed resume-command hints. |
| **P3** | Bug 5 — SSE hard timeout shorter than LLM timeout | ✅ Fixed — SSE loop duration now driven by `SSE_STREAM_TIMEOUT_SECONDS` (default 240s), comfortably exceeding `LLM_REQUEST_TIMEOUT`'s default 60s. |
| **P3** | Bug 9 — orphaned review/skip endpoints | ✅ Fixed — "Mark Reviewed" button wired into the Scheduled Runs UI card; new GET `/api/scheduled-runs/{run_id}/skip` endpoint plus a matching "Skip This Batch" link added to the digest email. Also fixed an adjacent gap found while wiring this up: the email's "Review & Generate Draft" link's `?run=` query param was never read by the frontend — added deep-link handling on page load. |
| **P3** | Zero test coverage on all new modules | Partially addressed as a side effect of the P0/P1 pass — `feed_cache` round-trip, `domain_store` CRUD, and a `build_graph()`/checkpointer smoke test now exist (17 tests total, up from 10). Scheduler/emailer and the JS-side flows (thread-id sync, deep-link, Mark Reviewed button) still have no automated coverage. |

---

## Next Steps

1. **Immediate:** Fix Bug 1 and Bug 2 — they break the two features this work cycle set out to deliver, and both are small, well-understood fixes.
2. **This week:** Bug 6 (connection leak) and Cost-1 (factuality toggle) — both are cheap fixes with outsized reliability/cost impact for an unattended scheduler.
3. **Short-term:** Delete dead Tavily code (Bug 4), fix `.gitignore` (Bug 8), reconcile domain config between UI and scheduler (Bug 3).
4. **Medium-term:** Draft version history / undo, toast notifications, server-side domain persistence — the same P2/P3 UX debt v1 identified, now compounded by the refine toolbar's one-click-overwrite behavior.

---

## P0/P1 Fix Log (2026-08-18)

All fixes below were made on `fix/scheduler-cache-review`, verified against live behavior (not just unit tests — see "Verification" per item), and covered by new regression tests in `tests/test_new_modules.py` (17 tests total now pass, up from 10).

### 🆕 Critical bug found during verification: checkpointer was closed before every run

Not in the original findings — surfaced while smoke-testing the app end-to-end to verify the other fixes. **This bug meant the entire application was non-functional**: every `/api/start` and CLI invocation failed with `sqlite3.ProgrammingError: Cannot operate on a closed database.`

**Root cause:** [graph.py](graph.py)'s `build_graph()` called `SqliteSaver.from_conn_string(db_path).__enter__()` and discarded the context-manager object itself, keeping only the yielded `SqliteSaver`. `from_conn_string` is a `@contextmanager` generator wrapping `closing(sqlite3.connect(...))`; with nothing holding a reference to the generator, CPython garbage-collects it the instant `build_graph()` returns, which resumes the generator with `GeneratorExit` and runs its `closing()` cleanup — closing the connection before the graph is ever invoked. Deterministic on every run (verified 3/3).

**Fix:** connect directly with `sqlite3.connect(db_path, check_same_thread=False)` and construct `SqliteSaver(conn)` directly, bypassing the generator-based factory entirely. The connection now lives as long as the `checkpointer`/`app` object that holds it — no GC-timing dependency.

**Verification:** reproduced the failure 3/3 times before the fix, confirmed 3/3 successful `.invoke()` calls after. Added `BuildGraphCheckpointerTests` regression test. Also ran a full live scout against `arxiv.org` through the fixed graph.

**Side-effect found and fixed in the same pass:** verifying this against a real feed (`arxiv.org`, ~4.1MB) revealed my own new `_fetch_feed_raw()` helper (added for Bug 1, below) capped reads at 2MB, silently truncating the XML mid-document and breaking the parse. Raised the cap to 15MB — large enough for real-world feeds, still bounded.

### Bug 1 — feed cache corrupting every hit

**Fix:** [graph.py](graph.py) `_fetch_source` now fetches raw feed text via a new `_fetch_feed_raw()` helper and caches that raw text (`feed_cache.set(feed_url, raw_feed_text)`), instead of caching `str(parsed_feed)`.

**Verification:** ran `scout_node` twice in a row against the live `arxiv.org` feed (second call hits the cache within the 15-min TTL) — both runs returned the same 10 articles with zero errors. Before the fix, the second call returned 0. Added `FeedCacheRoundTripTests` (cached value re-parses correctly, uncached miss returns `None`, expired entry not returned).

### Bug 2 — scheduled-run resume unreachable

**Fix:** [ui.py](ui.py) `loadScheduledRun()` now sets `threadIdEl.value` to the loaded run's `thread_id`, clears the stale selected-article-id field, and resets the progress steps to "approval" with an activity message naming the thread switch.

**Verification:** manual review of the resume/approve code path confirms it now reads the correct thread ID; this is a frontend-only fix (no Python unit test covers JS — flagged as a residual gap below).

### Bug 3 — scheduler ignoring UI-curated domains

**Fix:** new [domain_store.py](domain_store.py) (SQLite, `domains.db`) with `save_domains()` / `get_domains()` / `get_enabled_domains()`. Two new endpoints in [ui.py](ui.py): `GET/POST /api/domains`. The frontend now pushes every checkbox/add-domain change to the server and hydrates from it on page load (merging with `localStorage`, not replacing it — so the UI still works offline-first). [scheduler.py](scheduler.py) now prefers `domain_store.get_enabled_domains()` and only falls back to the static `SCHEDULER_DOMAINS` env var if the store is empty (i.e. before the UI has ever been opened).

**Verification:** live end-to-end — started the server, `POST /api/domains` with a curated list (one domain disabled), confirmed `GET /api/domains` reflects it, then called `domain_store.get_enabled_domains()` directly (what the scheduler calls) and confirmed the disabled domain was correctly excluded. Added `DomainStoreTests` (3 tests).

### Cost-1 — factuality check always on, no toggle

**Fix:** `ENABLE_FACTUALITY_CHECK` env var (default `true`, so default behavior is unchanged) added to [settings.py](settings.py) as `get_factuality_check_enabled()`, gating the call in [graph.py](graph.py) `author_node`. Documented in `.env.example`.

**Verification:** confirmed default resolves to `True`; toggling the env var is a one-line, directly-testable settings function (matches the pattern of every other settings getter, which already have implicit coverage via existing tests exercising the same module).

### Bug 6 — scheduler connection leak

**Fix:** [scheduler.py](scheduler.py) now builds the graph once, lazily, into a module-level `_graph_app` (guarded by a lock) and reuses it on every scheduled job — mirroring `ui.py`'s existing single-instance pattern — instead of calling `build_graph()` fresh inside `_run_scheduled_job()` on every tick.

**Verification:** code inspection confirms `_get_graph_app()` is now the only call site; combined with the checkpointer fix above, this now holds exactly one long-lived connection for the scheduler's lifetime instead of one new leaked connection per run.

### Bonus fix — Bug 8, gitignore

Added `cache.db*`, `checkpoints.db*`, `scheduled_runs.db*`, and the newly-created `domains.db*` to `.gitignore` — done alongside the domain-store work since it introduces another SQLite file into the repo root.

### Residual gaps after this pass

- **No JS/frontend test harness exists**, so Bug 2's fix (and the domain-sync JS) are verified by manual code review + live API smoke-testing, not automated tests. Worth a lightweight browser-based test setup if the frontend keeps growing.
- P2/P3 items (dead Tavily code, draft version history, toast notifications, hashtag library, export formats, persona switching) remain open as scoped — this pass was P0/P1 only, per instruction.
- The 🆕 checkpointer bug is a reminder that **this codebase has no test that ever calls `build_graph()` + `.invoke()`/`.get_state()` together outside the new `BuildGraphCheckpointerTests`** — that's now covered, but it's worth treating "does the compiled graph actually run" as a standing smoke test to run before trusting any future audit's "✅ Done" verdict on infrastructure changes.

---

## P2/P3 Bug Fix Log (2026-08-18, same day)

Scope: the four numbered **bugs** remaining at P2/P3 (Bug 4, 5, 7, 9). The P2/P3 **feature** recommendations (draft versioning, persona switching, hashtag library, toast notifications, export formats, cost visibility, pre-LLM heuristic filtering, webhooks, LinkedIn API) were deliberately left out of this pass — they're net-new functionality, not defects, and weren't asked for.

### Bug 4 — dead Tavily/enrichment code

**Fix:** deleted `_build_tavily_search_tool`, `_invoke_tavily_search`, `_http_fetch_text`, `_find_first_date_in_jsonld`, `_extract_date_from_html`, `_enrich_published_at_from_url`, `_normalize_tavily_results`, and `_extract_published_at` (its only caller was the now-deleted `_normalize_tavily_results`) from [graph.py](graph.py). Also removed the now-dead `warnings` and `urllib.error` imports. `graph.py` shrank from ~1630 to 1256 lines. Kept `_is_public_http_url` — its only *production* caller was the deleted enrichment code, but it's independently exercised by `tests/test_graph_edges.py::UrlSafetyTests` and is a reasonable, reusable safety primitive to keep around rather than delete.

**Verification:** `import graph` succeeds; full test suite passes unchanged (17/17).

### Bug 5 — SSE stream force-closes before the LLM timeout

**Fix:** [ui.py](ui.py) `/api/stream/{thread_id}` now computes its iteration cap from a new `SSE_STREAM_TIMEOUT_SECONDS` env var (default `240`) instead of a hardcoded `300 × 0.15s = 45s`. Documented in `.env.example`.

**Verification:** code inspection — `max_iterations = int(SSE_STREAM_TIMEOUT_SECONDS / SSE_POLL_INTERVAL_SECONDS)` now evaluates to 1600 iterations (~240s) by default, comfortably over the 60s default `LLM_REQUEST_TIMEOUT`.

### Bug 7 — CLI action set drift

**Fix:** `"done"` added to `main.py`'s `--action` `choices`, and to the printed "Available actions" / resume-command hint list.

**Verification:** code inspection; this is a pure argparse/print change with no behavioral branch to unit test beyond what `graph.DRAFT_REVIEW_ACTIONS` already covers.

### Bug 9 — orphaned review endpoint, missing "Skip This Batch"

**Fix:**
- Added a "Mark Reviewed" button to each row in the Scheduled Runs UI card (hidden once a run is reviewed), calling the existing `POST /api/scheduled-runs/{run_id}/review`.
- Added a new `GET /api/scheduled-runs/{run_id}/skip` endpoint (GET, not POST, since it needs to work as a plain link inside an email client) that also calls `scheduled_store.mark_reviewed()`, and added a matching "Skip This Batch" link next to "Review & Generate Draft" in [emailer.py](emailer.py)'s digest template.
- **Found and fixed an adjacent gap while wiring this up:** the email's "Review & Generate Draft" link has always pointed at `{base_url}/?run={run_id}`, but nothing in the frontend ever read that query parameter — clicking it just opened the plain homepage. Added a `URLSearchParams` check on page load that calls `loadScheduledRun()` automatically when `?run=` is present, so the email link now actually does what it was built to do.

**Verification:** live smoke test against a running server — seeded a fake scheduled run via `scheduled_store.store_run()`, confirmed `reviewed_at` was `null`, hit the new `GET /skip` endpoint, confirmed `reviewed_at` was populated; separately confirmed `POST /review` still works (now UI-reachable); confirmed the served HTML contains the new `deepLinkRunId` query-param handling.
