# Session Continuity

Use this file to resume quickly after context loss.

> **Resume here:** work is **PAUSED, not complete**, as of session `6830d7a1` (2026-08-30). All prior feature work (Phases 1-5) is now **committed and merged to `main`** via PRs #1 and #2. The only uncommitted items are `docker-compose.yml` (added a volume mount for `writer_examples.txt`) and `writer_examples.txt` itself (user-created, contains personal writing — not yet committed). This session added: thread drop-down, deepseek-v4-flash:cloud model default, collapsed Live State JSON, hybrid paywall exclusion (domain blocklist + RSS markers + opt-in fetch probe), and five writer-prompt improvements (anti-AI-tells, few-shot examples, free-text feedback, learn-from-edit, article body fetch). See "## Writer Prompt + Paywall Exclusion — Current State" below for exactly where things stand, "## Session Ledger" for the full session history, and `sessions/2026-08-30_6830d7a1.md` for this session's log.

## Session Ledger

Append-only — add one line per session, do not edit or reorder past entries.

- 2026-08-17/18 | session (unrecorded, pre-ledger) | fix/scheduler-cache-review | worktree: /home/toufic/Source/article-pipeline | Deep audit + P0/P1/P2/P3 bug fixes, committed as `64810ce` | log: none (predates this ledger)
- 2026-08-26 | session db626376 | fix/scheduler-cache-review | worktree: /home/toufic/Source/article-pipeline | Built & live-verified Phases 1-4 of the feature backlog (Docker mounts, toasts, cost tracking, draft versioning, personas, hashtags, pre-LLM prefilter, webhook trigger); all uncommitted; paused before Phase 5 | log: sessions/2026-08-26_db626376.md
- 2026-08-29 | session c3152374 | fix/scheduler-cache-review | worktree: /home/toufic/Source/article-pipeline | Built & live-verified Phase 5 (persistent style profile, multi-format output post/thread/carousel, performance dashboard Tier 1); video_script dropped per user, Tier 2 source attribution deferred; all uncommitted; 50 tests pass | log: sessions/2026-08-29_c3152374.md
- 2026-08-30 | session 6830d7a1 | main | worktree: /home/toufic/Source/article-pipeline | Committed & merged Phases 1-5 (PR #1, commit 1237c7a); added thread drop-down, deepseek-v4-flash:cloud model default, collapsed Live State JSON, hybrid paywall exclusion; added writer prompt improvements (anti-AI-tells, few-shot WRITER_EXAMPLES, free-text feedback, learn-from-edit, article body fetch) (PR #2, commit 6830d7a); 63 tests pass; docker-compose.yml + writer_examples.txt uncommitted | log: sessions/2026-08-30_6830d7a1.md

## Writer Prompt + Paywall Exclusion — Current State

**PAUSED / resume pending.**

All prior feature work (Phases 1-5) is committed and merged to `main`. This session added two batches of work (both merged via PRs #1 and #2), plus two uncommitted files.

### Uncommitted right now
```
 M docker-compose.yml          — added volume mount: ./writer_examples.txt:/app/writer_examples.txt
?? writer_examples.txt         — user's 4 real LinkedIn posts for few-shot examples; personal content, not code
```
Neither is blocking — the Docker container is running with both mounted. `docker-compose.yml` is safe to commit independently. `writer_examples.txt` should only be committed with explicit user permission (contains personal writing).

### What's live (merged to main)
- **Thread ID drop-down**: `GET /api/threads` + datalist, 10 most recent threads from `costs.db`.
- **Model default**: `deepseek-v4-flash:cloud` (was `llama3.1`).
- **Live State JSON**: collapsed by default (`<details>`).
- **Hybrid paywall exclusion**: 3 layers — `PAYWALLED_DOMAINS` (small blocklist, never medium.com/substack.com), `PAYWALL_MARKERS` (16 RSS-level phrases), `PAYWALL_PROBE=false` (opt-in fetch probe). Distinct drop reasons in scout audit.
- **Writer prompt rewrite**: collapsed labelled sections into editorial brief; added `_ANTI_AI_TELLS` constraints (graph.py:86); removed synthetic persona examples from prompt.
- **`WRITER_EXAMPLES`**: env var (inline text or `file:` path), injected as few-shot. User configured `file:/app/writer_examples.txt` with 4 real posts.
- **Free-text feedback**: textarea + "Refine with Feedback" button next to Quick Refine (ui.py:599).
- **Learn-from-edit**: `POST /api/learn-from-edit` compares published draft vs original AI draft, proposes style rule via LLM, toast offers one-click save.
- **Article body fetch**: `author_node` fetches full article body (200KB cap, fail-open) instead of 1600-char RSS summary.

### Dead ends (don't retry)
- Don't add medium.com or substack.com to `PAYWALLED_DOMAINS` — user explicitly rejected; they carry huge free technical content.
- Don't put multi-line `WRITER_EXAMPLES` directly in `.env` for Docker — docker compose's parser can't handle it. Use `file:` prefix.
- Don't re-inject the old synthetic persona examples into the prompt — they made output sound MORE AI (model pattern-matched the example's structure).

### Open questions for the user
- Whether to commit `writer_examples.txt` (personal content) and `docker-compose.yml`.
- Whether to test the new writer prompt against a real article run and tune further.

- Worktree: /home/toufic/Source/article-pipeline
- Next step: Commit `docker-compose.yml` (and `writer_examples.txt` if user approves), then test the new writer prompt with a real article run to evaluate output quality.

## Feature Backlog (Phases 1-5) — COMPLETED

**All 5 phases committed and merged to `main` via PRs #1 and #2.**

`FEATURE_BACKLOG_PLAN.md` (local, uncommitted, not in git) lays out 5 phases on top of `AUDIT_REPORT_2026-08-18.md`'s backlog. All five phases are fully implemented and live-verified but **not committed** — see `git status --short` for the exact file list. Nothing is broken; it's uncommitted because no one has said "commit and push" yet, not because it's incomplete or failing. Full test suite (50 tests) passes.

Phases 1-4 (session `db626376`, 2026-08-26): Docker volume mounts for all 6 SQLite stores; toast notifications; LLM cost/token tracking; draft version history with click-to-restore; .txt/.md export; 3-persona voice switching; static hashtag-chip library; pre-LLM heuristic prefilter; secret-gated webhook trigger endpoint. Two live-only bugs fixed along the way (retired Gemini model default, analyst fallback-prompt schema mismatch) plus the `ui.py` backslash-escape hazard. Full detail in `sessions/2026-08-26_db626376.md`.

Phase 5 (session `c3152374`, 2026-08-29, explain-then-approve mode):
- **Item 1 — Persistent style profile** (`style_profile.py`): SQLite store for plain-text style rules (persona `*` = all, or specific). Rules injected into `_build_author_prompt` + `/api/refine` as a `Standing style rules:` block. `applied_count` tracked per rule. Promote-on-refine: after any Quick Refine, a 6-second toast offers one-click "Save as style rule" (not auto-saved). Explicit-curation only — no automatic quality scoring, no LLM-judge, no thumbs-up/down. 7th SQLite store (`style_profile.db`).
- **Item 2 — Multi-format output** (`FORMATS` dict in `graph.py`): 3 formats (`post`/`thread`/`carousel`). Format is orthogonal to persona — persona = voice, format = shape. `post` format falls back to the persona's structure/example → byte-identical to pre-format output (no regression). `video_script` was in the proposed design but dropped per user. Threaded through `AgentState.format`, `/api/start`, `/api/refine`, scheduler, webhook. 5 tests.
- **Item 3 — Performance dashboard** (`dashboard.py`): read-only aggregation across `cost_tracker`/`draft_store`/`scheduled_store`/`style_profile`. Five sections: cost (by node/provider, daily), runs (weekly rollup with email/review rates, topic distribution), drafts (published count, avg length, refine/author/manual counts), style rules (applied counts). `GET /api/dashboard?days=30`, collapsible UI panel, on-demand refresh. Tier 2 (source-level attribution — which domain produces the most published drafts / highest-scored articles) deferred per user: needs adding `source_domain` to `published_drafts`, a schema change to a store already in production. 6 tests.

Not done: LinkedIn API direct-publish (Phase 4b) — user chose "skip for now" in the prior session, not revisited this session. Dashboard Tier 2 (source attribution) — explicitly deferred.

Dead ends / standing warnings (don't re-derive): don't raise `PREFILTER_MIN_SUMMARY_CHARS` back toward 150 (live-verified wrong); don't default to `gemini-2.0-flash` (retired); don't add a naive `test_webhook.py` to the shared `discover` run (import-order DB contamination); `ui.py` backslash-escape hazard still applies. Full detail in both session logs.

- Worktree: /home/toufic/Source/article-pipeline
- Next step: The feature backlog is complete. The only open decisions are (a) whether/when to commit the working tree and open a PR for `fix/scheduler-cache-review` → `main` (now carries Phases 1-5 on top of `64810ce`, nothing ever merged), and (b) whether to revisit LinkedIn API direct-publish (still deferred, needs user OAuth consent an agent can't do alone). Neither is blocking — nothing is broken.

## Current Project State

- Project: `article-pipeline`
- Current branch: `main` (latest commit: `9d2b714` — Merge PR #2). All feature work is now on `main`.
- Core workflow: `scout -> analyst -> approval(interrupt) -> author -> edit_approval(interrupt)`, now with scheduled runs *and* webhook-triggered runs feeding the same `approval` interrupt. Scout now includes hybrid paywall exclusion. Author now fetches article body and uses anti-AI-tells + optional few-shot examples.
- Checkpointing: `SqliteSaver` (`checkpoints.db`), **not** `MemorySaver` — survives restarts.
- CLI + UI both support start/resume flows; UI now also has a scheduled-runs review panel, draft version history, persona switching, format switching (post/thread/carousel), a hashtag chip library, cost tracking, a style rules panel, a performance dashboard, a thread ID drop-down, custom feedback textarea, and learn-from-edit on publish.
- Project is treated as a personal localhost tool, not a cloud/SaaS service.
- Latest **commit**: `9d2b714` — "Merge pull request #2 from guzziride/improvement/writer-prompt". Two uncommitted files remain (`docker-compose.yml` volume mount + `writer_examples.txt`).

## What Happened This Session (2026-08-17 → 2026-08-18)

The session opened on top of a large amount of **uncommitted, undocumented work** already sitting in the working tree from a previous session (scheduler, email digest, RSS cache, SqliteSaver migration, SSE streaming, multi-axis analyst scoring, refine toolbar, factuality check) — none of which was reflected in the continuity docs at the time. The work this session went through four phases:

### Phase 1 — Deep audit
Read every source file, cross-referenced the existing `AUDIT_REPORT.md` (v1) against what was actually implemented, and wrote `AUDIT_REPORT_2026-08-18.md` (v2). Found:
- 8 of 17 v1 recommendations genuinely done, 2 built-but-broken, 1 half-done, 7 never started.
- **Bug 1**: `feed_cache.py` cached `str(parsed_feed)` instead of raw feed text — every cache hit silently returned zero articles for that domain (confirmed via a live `feedparser` round-trip test).
- **Bug 2**: loading a scheduled run in the UI never synced the Thread ID field, so the resume/approve flow could never reach the actually-paused thread.
- **Bug 3**: the scheduler read a static `SCHEDULER_DOMAINS` env var, completely disconnected from the domains the user curates in the UI.
- **Bug 4**: ~380 lines of dead Tavily/URL-enrichment code, unreachable from the active scout path, referencing a dependency (`langchain_community`) already removed from `requirements.txt`.
- **Bug 5**: SSE draft-streaming endpoint hard-closed after 45s — shorter than the default 60s LLM timeout.
- **Bug 6**: `scheduler.py` called `build_graph()` fresh on every cron tick, leaking a SQLite connection each time.
- **Bug 7**: `main.py --action` choices didn't include `"done"`, out of sync with the graph's actual action set.
- **Bug 8**: `cache.db`, `checkpoints.db`, `scheduled_runs.db` weren't in `.gitignore`.
- **Bug 9**: the "mark reviewed" endpoint existed server-side with no UI button, and the spec'd "Skip This Batch" email link was never built.
- **Cost-1**: every draft generation was silently running 2 LLM calls (draft + always-on factuality check) with no toggle or cost visibility anywhere.

### Phase 2 — P0/P1 fixes
Created branch `fix/scheduler-cache-review`. Fixed Bugs 1, 2, 3, 6, 8, and Cost-1. **While verifying these fixes live** (not just via unit tests), found and fixed a bug **not in the original audit**: `build_graph()`'s `SqliteSaver.from_conn_string(...).__enter__()` pattern was discarding the context-manager generator, which gets garbage-collected almost immediately — closing the SQLite connection before the graph was ever invoked. **This meant the entire app was non-functional**; every `/api/start` call failed with `Cannot operate on a closed database.` Fixed by connecting directly with `sqlite3.connect(check_same_thread=False)` + `SqliteSaver(conn)`. Also caught a truncation bug in the new fix's own feed-fetch helper (2MB read cap was too small for arXiv's ~4MB feed) while live-testing against a real feed.

New: `domain_store.py` (SQLite domain config shared between UI and scheduler via `/api/domains`), `ENABLE_FACTUALITY_CHECK` env toggle, `scheduler.py` now reuses one graph instance instead of building fresh per job. Added 7 regression tests (`tests/test_new_modules.py`) — feed-cache round-trip, domain-store CRUD, and a checkpointer smoke test that would have caught the GC bug.

### Phase 3 — P2/P3 bug fixes
Fixed the remaining numbered bugs (4, 5, 7, 9) — explicitly scoped to *bugs*, not the P2/P3 *feature* backlog (draft version history, persona switching, hashtag library, toast notifications, export formats, webhook triggers, LinkedIn API — all still open, see `AUDIT_REPORT_2026-08-18.md`). Deleted the dead Tavily code, made the SSE timeout configurable (`SSE_STREAM_TIMEOUT_SECONDS`), synced the CLI action set, and wired up "Mark Reviewed" + "Skip This Batch" in the UI/email. While wiring up Bug 9, found and fixed an adjacent gap: the email's "Review & Generate Draft" link's `?run=` query param was never read by the frontend at all — added deep-link handling on page load.

### Phase 4 — Commit, push, docs sync
All 17 tests pass. Committed as `64810ce` and pushed to `origin/fix/scheduler-cache-review`. This document and `processing-details.md` were then rewritten to reflect the current implementation (previously both were stale relative to the working tree even before this session started).

## Important Behavior Now

- Checkpointing is `SqliteSaver` (`checkpoints.db`), not `MemorySaver` — durable across restarts. **Do not** revert to the `from_conn_string(...).__enter__()` pattern; see `processing-details.md` §1 for why.
- Scout fetches all selected domains concurrently (`ThreadPoolExecutor`, `SCOUT_MAX_WORKERS=6` default) and caches raw feed responses for 15 minutes (`feed_cache.py`).
- Draft generation streams token-by-token to the UI via SSE, followed by an LLM-based factuality check (toggle: `ENABLE_FACTUALITY_CHECK`).
- Analyst scoring is multi-axis (contrarian value, technical depth, debate potential, timeliness, source credibility) with few-shot examples in the prompt.
- A Quick Refine toolbar (5 one-click LLM re-prompts) sits above the draft editor. Every distinct draft state (initial author draft, each refine, each manual edit) is now snapshotted to `draft_store.py`'s `draft_versions` table and browsable/restorable via the "Version History" panel — this **is** the undo mechanism (click an older version to restore it; append-only, doesn't delete newer versions).
- Author voice is selectable per run via `persona` (`cto_phd` default / `startup_founder` / `practitioner_engineer`, defined in `graph.py`'s `PERSONAS` dict) — persists through the thread, honored by both the initial draft and `/api/refine`.
- A static 16-hashtag chip library sits under the draft editor for fast manual insertion — purely client-side, does not affect LLM generation.
- "Download .txt" / "Download .md" buttons export the current draft client-side (no backend round-trip).
- Every LLM call (analyst, author, factuality, refine) now logs token usage + an approximate USD cost via `cost_tracker.py` (`costs.db`); surfaced via `GET /api/costs/{thread_id}` and `/api/costs/summary`, and a summary line in the UI. Costs are approximate, not billing-accurate.
- `analyst_node` runs a pre-LLM heuristic prefilter (`graph._heuristic_prefilter`, `PREFILTER_MIN_SUMMARY_CHARS = 40`) before the analyst LLM call, dropping obvious junk (empty/placeholder summaries, funding-announcement/press-release titles) — **do not raise the 40-char threshold back toward 150** without re-verifying against a real feed first; 150 was tried and live-verified to drop 8/10 legitimate TechCrunch articles.
- Scheduled runs (`scheduler.py`, `APScheduler`, disabled by default via `SCHEDULER_ENABLED=false`) run scout+analyst unattended on a cron schedule, stop at the `approval` interrupt, store candidates, and email an HTML digest. The review loop (email → UI → resume → draft) works end-to-end. The scheduler uses the same domain list the UI is configured with (via `domain_store.py`), not a separate static env var. Now also supports `SCHEDULER_PERSONA`.
- **New**: `POST /api/webhook/trigger` (external/unattended trigger, e.g. Zapier/n8n) — disabled by default (501) unless `WEBHOOK_SECRET` is set; requires header `X-Webhook-Secret` to match. Shares the exact same review pipeline as scheduled runs (`scheduler.run_scout_analyst_job`, used by both) — a webhook-triggered run shows up in the Scheduled Runs panel and gets emailed the same way.
- **New (Phase 5)**: Persistent user style profile (`style_profile.py`, `style_profile.db` — 7th SQLite store). Plain-text rules (persona `*` or specific) injected into every author + refine prompt as a `Standing style rules:` block. `applied_count` tracked per rule. Promote-on-refine: after any Quick Refine, a 6-second toast offers one-click "Save as style rule" (not auto-saved). Explicit-curation only — no automatic quality scoring. CRUD via `GET/POST/PATCH/DELETE /api/style-rules`. Rules are advisory — the LLM may deviate.
- **New (Phase 5)**: Multi-format output (`FORMATS` dict in `graph.py`). 3 formats: `post` (default, byte-identical to legacy), `thread` (5-7 numbered posts), `carousel` (6-8 slides). Format is orthogonal to persona — persona = voice, format = shape. Threaded through `AgentState.format`, `/api/start`, `/api/refine`, scheduler (`SCHEDULER_FORMAT`), webhook (`WEBHOOK_FORMAT`). `video_script` was proposed but dropped per user. `post` format falls back to the persona's structure/example (no regression for existing runs).
- **New (Phase 5)**: Performance dashboard (`dashboard.py`). Read-only aggregation across `cost_tracker`/`draft_store`/`scheduled_store`/`style_profile` via `GET /api/dashboard?days=30`. Five sections: cost (by node/provider, daily), runs (weekly rollup with email/review rates), drafts (published count, avg length, refine/author/manual counts), topic distribution, style rule usage. Collapsible UI panel, on-demand refresh (no auto-polling). Tier 2 (source-level attribution) deferred — needs `source_domain` added to `published_drafts` (schema change to a store in production).
- Draft review is fail-closed: invalid actions and empty edits don't publish. CLI and UI action sets are in sync (`publish`/`edit`/`pick_another`/`done`).
- User-facing notifications use a toast system (`toast()` in `ui.py`'s JS), not `window.alert()` — the 2 `confirm()` dialogs (delete draft, reset thread) are unchanged, those are still native blocking confirmations by design.
- Tests: 50 stdlib `unittest` tests across `tests/test_graph_edges.py`, `tests/test_new_modules.py`, `tests/test_cost_tracker.py`, `tests/test_style_profile.py`, `tests/test_formats.py`, and `tests/test_dashboard.py`, all passing as of the last run. **No automated test exists for `/api/webhook/trigger`** — deliberate, not an oversight; see the session log (`sessions/2026-08-26_db626376.md`) for why and what it would take to add one safely.
- `.gitignore` now covers all seven SQLite files the app creates (`drafts.db`, `cache.db`, `checkpoints.db`, `domains.db`, `scheduled_runs.db`, `costs.db`, `style_profile.db`).
- `docker-compose.yml` now bind-mounts all seven SQLite files (was only `drafts.db` before this session).
- **Hazard**: `ui.py`'s entire HTML/JS page is one large non-raw Python triple-quoted string. Any new JS regex/string literal containing backslash escapes (`\d`, `\w`, `\b`, `\t`, `\n`, `\s`, etc.) must be double-backslashed in the Python source (e.g. `\\t`) or Python will silently mangle it before it ever reaches the browser — for escapes that happen to also be valid Python escapes (`\t`, `\n`), there is no warning at all, just silently broken JS on the served page. This bit this session once (the hashtag-chip toggle function); caught only by inspecting the actual served page source, not by any test.

## Open Items / Next Steps

- **Commit `docker-compose.yml` + `writer_examples.txt`**: two uncommitted files from the writer-prompt session. `docker-compose.yml` is safe to commit independently. `writer_examples.txt` contains the user's personal LinkedIn posts — commit only with explicit user approval.
- **Test the new writer prompt**: run a real article through the pipeline with `WRITER_EXAMPLES` active and evaluate whether the output sounds less AI. The user's primary motivation was "the content is very AI looking."
- **Dashboard Tier 2 (deferred)**: source-level attribution — needs `source_domain` added to `published_drafts`. Not blocking.
- **LinkedIn API direct-publish**: still deferred from a prior session.
- Full detail on all decisions and dead ends from this session is in `sessions/2026-08-30_6830d7a1.md`. Prior session detail: `sessions/2026-08-26_db626376.md` (Phases 1-4), `sessions/2026-08-29_c3152374.md` (Phase 5).

## Quick Resume Commands

```bash
cd /home/toufic/Source/article-pipeline
git checkout main
source .venv/bin/activate
pip install -r requirements.txt
```

Start UI:

```bash
uvicorn ui:web_app --reload --host 0.0.0.0 --port 3010
```

Optional preflight:

```bash
python preflight.py --analyst-provider gemini --writer-provider openai --check-live
```

Run the full test suite:

```bash
.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

CLI start (interrupt expected):

```bash
python main.py --thread-id demo-1 --show-state
```

CLI resume:

```bash
python main.py --thread-id demo-1 --selected-article-id 1 --human-feedback "Focus on SaaS implications" --show-state
```

CLI draft review (now includes `done`):

```bash
python main.py --thread-id demo-1 --action publish
python main.py --thread-id demo-1 --action done
```

## Key Files To Read First On Resume

1. `SESSION_CONTINUITY.md` (this file) — start with the blockquote pointer and Session Ledger at the top
2. `sessions/2026-08-30_6830d7a1.md` — this session's log (writer prompt + paywall exclusion)
3. `sessions/2026-08-26_db626376.md` — full handoff detail for Phases 1-4
4. `sessions/2026-08-29_c3152374.md` — Phase 5 session log
5. `processing-details.md` — technical implementation detail (§15 = Phases 1-4, §16 = Phase 5, §17 = writer prompt + paywall)
6. `FEATURE_BACKLOG_PLAN.md` — the 5-phase plan; all phases done
7. `AUDIT_REPORT_2026-08-18.md` — original bug list and backlog
8. `graph.py` — scout, analyst, author nodes; `_build_author_prompt`, `_ANTI_AI_TELLS`, `_is_paywalled_article`, `PERSONAS`, `FORMATS`
9. `settings.py` — all env-backed config including `PAYWALLED_DOMAINS`, `PAYWALL_MARKERS`, `PAYWALL_PROBE`, `WRITER_EXAMPLES`
10. `ui.py` — full UI; note the `custom-refine` textarea, `learn-from-edit` flow, thread drop-down, collapsed Live State JSON
11. `style_profile.py`, `dashboard.py`, `cost_tracker.py` — newer subsystems
