# Session Continuity

Use this file to resume quickly after context loss.

## Current Project State

- Project: `article-pipeline`
- Core workflow is active: `scout -> analyst -> approval(interrupt) -> author -> edit_approval(interrupt)`
- Checkpointing: LangGraph `MemorySaver` (in-memory only)
- CLI + UI both support start/resume flows
- Project is treated as a personal localhost tool, not a cloud/SaaS service.

## Major Changes Completed In This Session

1. Added continuity docs:
   - `processing-details.md`
   - `SESSION_CONTINUITY.md`
2. Fixed date-drop visibility:
   - richer date extraction
   - expanded dropped-article audit in UI
3. Added URL metadata enrichment fallback for missing publish dates.
4. Replaced Tavily-first scout with **domain-routed ingestion**:
   - RSS for supported domains
   - Google News RSS for no-feed/unknown domains
5. Added dependencies for new scout stack:
   - `feedparser`
6. Mitigated model request-size failures:
   - analyst input capped (`ANALYST_MAX_ARTICLES=20`)
   - summary truncation for analyst/author prompts
7. Improved UI feedback:
   - button loading states
   - progress step indicator
   - clearer activity messages
8. Added article volume controls:
   - `RSS_MAX_ITEMS_PER_FEED` (default `25`)
   - `SCOUT_MAX_TOTAL_ARTICLES` (default `80`)
9. Added cloud-hosted Ollama support:
   - Support for `OLLAMA_API_KEY` via Authorization headers in `graph.py`.
   - Dynamic model dropdowns in UI driven by `OLLAMA_MODEL_OPTIONS` in `.env`.
10. Repository Management:
    - Initialized Git and pushed to GitHub: `https://github.com/guzziride/article-pipeline`.
11. Dockerization:
    - Added `Dockerfile` and `docker-compose.yml`.
    - Fixed `ImportError` by adding `langchain-ollama` and updating `graph.py`.
    - Application now runs in Docker at `http://localhost:3010` with persistence.
12. User topic is now end-to-end across all three paths:
    - Added `_topic_keywords()` and `_topic_matches()` helpers in `graph.py`.
    - RSS entries that do not match the topic are dropped with reason `topic_mismatch`.
    - Google News RSS fallback receives the same client-side topic filtering (word-boundary regex match).
    - Analyst prompt no longer hardcodes MCP/agentic/SaaS themes; it reads `state["topic"]` and adds the persona framing `"Assume the reader is a technical founder or CTO evaluating strategic impact."`.
    - `dropped_topic_mismatch` added to per-source RSS/Google News stats and audit trail.
13. Topic keyword matching refinements:
    - Word-boundary regex (`\bkeyword\b`) replaces naive substring containment.
    - Minimum keyword length removed so acronyms (`AI`, `ML`, `QA`) survive.
    - Google News RSS fallback uses `_topic_search_terms()` plus `site:<domain>` and `when:<MAX_ARTICLE_AGE_DAYS>d`.
14. Docker rebuilt and restarted with all changes.
15. Live source log for scout: `progress.py` now appends each source label to a `source_log` array; `/api/progress` returns it; UI renders pills under "Sources Queried" card on every 1.2s poll.
16. Analyst parsing fix: `_invoke_analyst_structured` now catches any `Exception` (not just `NotImplementedError`), falling back to a re-prompt with the exact expected shape. `_extract_json_payload` handles JSON arrays by wrapping them as `{"picks": [...]}`.
17. Created `AGENTS.md` — condensed reference for agent context (architecture, entrypoints, key files, state shape, UI layout, test/CLI commands, provider quirks, gotchas).
18. Localhost-focused audit follow-up implemented:
    - Added `DRAFT_REVIEW_ACTIONS` and `_apply_draft_review_action()` in `graph.py`.
    - `action="edit"` without `edited_draft` now raises `ValueError` instead of falling through to publish.
    - Unknown draft-review actions now raise `ValueError` instead of publishing.
    - Publish now only happens for explicit `action="publish"`.
    - Added `_is_public_http_url()` guard before URL metadata enrichment; non-HTTP(S), localhost, private, loopback, and link-local targets are skipped.
    - Analyst prompt now uses configured `MAX_ARTICLE_AGE_DAYS` instead of hardcoded `14`.
    - Added lightweight stdlib tests in `tests/test_graph_edges.py`.
19. Tavily removed from active scout path:
    - Unknown and no-feed domains now use Google News RSS fallback.
    - `venturebeat.com` now uses direct RSS (`https://venturebeat.com/category/ai/feed`).
    - `langchain-tavily` and `tavily-python` were removed from `requirements.txt`.
    - Added Google News fallback tests in `tests/test_graph_edges.py`.
 20. Google News fallback loosened after user reported `0 / 0` source counts:
     - `_build_google_news_rss_url()` now uses only `site:<domain>` and `when:<MAX_ARTICLE_AGE_DAYS>d`.
     - Topic filtering remains local via `_topic_matches()` after retrieval.
     - Direct RSS added for `searchengineland.com`, `searchenginejournal.com`, `marketingbrew.com`, `kopp-online-marketing.com`, and `semrush.com`.
     - Smoke-tested reported domains; all now return source entries before local filters.
 21. UI responsiveness fixes — page did not fit/adapt to the browser window:
     - `.row` now uses `flex-wrap: wrap` so domain rows reflow.
     - Grids use `minmax(min(..., 100%), 1fr)` for single-column fallback on narrow viewports.
     - Progress row uses `auto-fit` `minmax`.
     - Cards, text, and JSON blocks use `min-width: 0` and `overflow-wrap: anywhere` for long domain names and API responses.
 22. Resume behavior hardened — `Approve + Generate Draft` now regenerates the draft on every click:
     - `/api/resume` (`ui.py:1370`) detects when the graph is paused at `edit_approval` and first issues `Command(resume={"action": "pick_another"})` so the user re-enters the approval step before the new article is selected.
     - `edit_approval_node()` (`graph.py:1424`) rejects dict resumes without an explicit `action`.
 23. UI button label changed from `Generate Draft` to `Approve + Generate Draft` (`ui.py:450`) so the regenerate-on-click behavior is obvious.
 24. Author prompt refactored from many concatenated string literals into a single triple-quoted f-string at `graph.py:1365`. The persona/voice block, structure, and style injection are all in one editable location; human feedback is appended as the final `Human feedback: {feedback or 'None'}` line.
 25. Tests expanded to 10 (added Google News URL builder test and `edit_approval_node` malformed-resume rejection test). All 10 tests pass locally and in the container.

## Important Behavior Now

- Tavily is no longer used by the active scout path.
- RSS and Google News RSS require no search API key.
- Scout debug now exposes routing, source-level stats, drop audits, and errors.
- Draft review is fail-closed: invalid actions and empty edits no longer publish.
- URL enrichment is guarded and only fetches public `http`/`https` URLs.
- Tests exist now and run with stdlib `unittest` (10 tests, all passing).
- The "Approve + Generate Draft" button always regenerates the draft, including when pressed after `edit_approval`.
- The author prompt is a single triple-quoted f-string in `graph.py:1365`; the entire persona/voice block lives in one place.
- The UI is responsive: long domain names, narrow viewports, and tall JSON blocks no longer overflow.

## Latest User-Reported Problem (Resolved)

- User report: UI page did not fit/adapt to the browser window; long domain names overflowed.
- **Status:** Resolved. CSS was patched (`.row` flex-wrap, `minmax(min(..., 100%), 1fr)`, `min-width: 0`, `overflow-wrap: anywhere`, etc.) and the UI now reflows cleanly.
- Follow-up changes: `Approve + Generate Draft` button now reliably regenerates the draft from any resume state; author prompt is a single triple-quoted f-string for easier editing.
- Verification: `.venv/bin/python -m unittest tests/test_graph_edges.py` → `10 tests OK` (locally and in container).

## Quick Resume Commands

```bash
cd /home/toufic/Source/article-pipeline
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

Run local edge tests:

```bash
.venv/bin/python -m unittest tests/test_graph_edges.py
```

CLI start (interrupt expected):

```bash
python main.py --thread-id demo-1 --show-state
```

CLI resume:

```bash
python main.py --thread-id demo-1 --selected-article-id 1 --human-feedback "Focus on SaaS implications" --show-state
```

## Key Files To Read First On Resume

1. `SESSION_CONTINUITY.md`
2. `processing-details.md`
3. `graph.py`
4. `ui.py`
5. `settings.py`
