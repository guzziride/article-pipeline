# AGENTS.md — article-pipeline

## Quick start

```bash
cp .env.example .env   # then fill in API keys
uvicorn ui:web_app --reload --host 0.0.0.0 --port 3010
```

Docker: `docker compose up --build -d` (publishes port 3010).

Scope: personal localhost tool. Do not add cloud/SaaS infrastructure, auth systems, queues, or broad production hardening unless explicitly requested.

## Architecture

LangGraph HITL workflow: `scout → analyst → approval(interrupt) → author → edit_approval`.

- **scout** (`graph.py:1044`): domain-routed ingestion — RSS for known feeds (`DOMAIN_INGESTION_MAP`), Google News RSS for no-feed/unknown domains. Scans all checked domains in the UI, deduplicates, caps at `SCOUT_MAX_TOTAL_ARTICLES` (80).
- **analyst** (`graph.py:1206`): LLM ranks top articles; structured output with fallback JSON extraction.
- **approval** (`graph.py:1292`): always interrupts for human selection.
- **author** (`graph.py:1339`): LLM generates LinkedIn draft. Prompt is a single triple-quoted f-string in `author_node()`.
- **edit_approval** (`graph.py:1468`): second interrupt for publish/edit/pick_another/done.

Checkpointing: `MemorySaver` (in-memory, lost on restart). Thread ID is the resume key.

## Entrypoints

| Entry | File | Notes |
|-------|------|-------|
| UI (FastAPI) | `ui.py` | HTML/JS served at `/`. API at `/api/*`. |
| CLI | `main.py` | Supports start and resume via `--thread-id`. |
| Preflight | `preflight.py` | Env + service checks. |

## Key files

| File | Role |
|------|------|
| `graph.py` | State schema, all nodes, routing, model selection. |
| `settings.py` | Env-backed config (topic, age limits, volume caps). |
| `ui.py` | FastAPI app, HTML template, JS frontend. |
| `draft_store.py` | SQLite persistence for published drafts (`drafts.db`). |
| `progress.py` | In-memory per-thread progress tracking (thread-safe). |
| `tests/test_graph_edges.py` | Stdlib edge tests for draft actions, URL safety, topic matching, and analyst prompt recency. |

## State shape (`AgentState` in `graph.py`)

- `raw_articles` — list with `operator.add` reducer (accumulates across checkpoints)
- `curated_candidates` — analyst picks
- `scout_debug` — routing, per-source stats, errors, URL audit
- `topic`, `include_domains`, `analyst_provider`, `writer_provider`, `analyst_model`, `writer_model` — runtime controls
- `thread_id` — used for progress tracking

## UI layout (in order)

1. Start Flow — thread ID, providers, topic, domain checkboxes
2. Articles Retrieved by Source — **live** per-source breakdown (updates via 1.2s poll)
3. Curated Candidates (Approval Step)
3b. Raw Articles (All Scout Results)
4. Draft Review & Publish
5. Sources Queried — live pill log
6. Live State JSON
7. Scout Debug + Dropped Articles Audit

## Test commands

Tests use stdlib `unittest`; no pytest dependency is required. Verify edge behavior with tests, and verify end-to-end behavior with a CLI or UI scan when needed.

```bash
# Local edge tests (10 tests)
.venv/bin/python -m unittest tests/test_graph_edges.py

# In container
docker compose exec -T article-pipeline python -m unittest tests/test_graph_edges.py

# CLI full run (interrupts at approval)
python main.py --thread-id test-1 --analyst-provider ollama --writer-provider ollama

# Resume after interrupt
python main.py --thread-id test-1 --selected-article-id 1

# Preflight
python preflight.py --check-live
```

## Provider notes

- Ollama is the default for both analyst and writer.
- Tavily is no longer used by the active scout path; RSS and Google News RSS require no search API key.
- Cloud Ollama: set `OLLAMA_API_KEY` + `OLLAMA_BASE_URL`; model options driven by `OLLAMA_MODEL_OPTIONS` env var.
- `_get_chat_model(provider, role, model_override)` in `graph.py:166` — shared by analyst, author, and health check.

## Gotchas

- **Scout runs fully synchronously** — no streaming. Progress pushed via in-memory `progress.py`.
- **`source_results` in progress tracker** is the live feed for the "Articles Retrieved by Source" UI section. Populated per-source in `scout_node` via `record_source_result()`.
- **Google News fallback** — unknown/no-feed domains use broad `_build_google_news_rss_url()` queries with only `site:<domain>` and `when:<MAX_ARTICLE_AGE_DAYS>d`; topic filtering is applied locally after retrieval to avoid zero-result over-constrained Google News queries.
- **Draft review is fail-closed** — `_apply_draft_review_action()` rejects unknown actions and empty `edit`; only explicit `publish` publishes.
- **URL enrichment guard** — `_is_public_http_url()` skips non-HTTP(S), localhost, private, loopback, and link-local targets before `_http_fetch_text()`.
- **`drafts.db`** is SQLite with WAL mode. Mounted as volume in Docker (`./drafts.db:/app/drafts.db`). Schema: `published_drafts` table.
- **Domain persistence** is in browser `localStorage` (`article_pipeline_domains`, `article_pipeline_domains_disabled`) — not on the server.
- **Topic filtering** uses word-boundary regex (`\bkeyword\b`) against title+summary. Stop-words removed. No minimum keyword length.
- **Analyst LLM prompt** has a `ANALYST_MAX_ARTICLES=20` cap and `ANALYST_SUMMARY_MAX_CHARS=260` truncation to avoid provider 400 errors. Recency wording uses configured `MAX_ARTICLE_AGE_DAYS`. Author summaries truncated at `AUTHOR_SUMMARY_MAX_CHARS=1600`.
- **Structured output retry**: `_invoke_analyst_structured` tries `with_structured_output` first; on any exception, falls back to re-prompt with explicit JSON schema + `_extract_json_payload` wrapper.
- **Resume regenerates drafts** — `/api/resume` with `selected_article_id` always regenerates the draft. If the graph is currently paused at `edit_approval`, the resume first issues `Command(resume={"action": "pick_another"})` so the user re-enters the approval step before the new article is selected (`ui.py:1389`).
- **Resume button label** is `Approve + Generate Draft` (`ui.py:450`); pressing it after `edit_approval` reopens approval and produces a new draft.
- **`edit_approval_node()` rejects malformed resumes** — a `dict` payload without an explicit `action` raises `ValueError`; `pick_another`/`edit`/`publish`/`done` are the only accepted actions (`graph.py:1424`).
- **UI is responsive** — `.row` uses `flex-wrap`, grids use `minmax(min(..., 100%), 1fr)`, progress row uses `auto-fit` minmax, cards/text use `min-width:0` and `overflow-wrap:anywhere`. The layout adapts to narrow viewports and long domain names.
- **Author prompt is a single f-string** — the entire LinkedIn writer prompt lives in one triple-quoted f-string at `graph.py:1365`; human feedback is appended as `Human feedback: {feedback or 'None'}` at the end of the prompt with no structural parsing.
- **No CI/CD** — no workflow files, no linter/formatter config, no pre-commit hooks. Run local `unittest` manually.
