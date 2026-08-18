# Processing Details: Article Pipeline

This file reflects the current implementation as of this session.

## 1) Purpose and Current Pipeline

`article-pipeline` is a LangGraph HITL workflow for technical-news discovery and LinkedIn drafting:

1. `scout` gathers recent articles.
2. `analyst` ranks top candidates.
3. `approval` interrupts for human selection.
4. `author` drafts the final post.
5. `edit_approval` interrupts for publish/edit/pick-another/done.

Graph topology in `graph.py`:
- `START -> scout -> analyst -> (approval or END) -> author -> edit_approval -> (approval, edit_approval, or END)`
- `MemorySaver` checkpointing, resume by reusing `thread_id`.
- This is a personal localhost workflow; do not add cloud-scale infra unless explicitly requested.

## 2) Key Implementation Shift (Important)

Scout no longer uses Tavily in the active path.

Current ingestion is **domain-routed dual strategy**:
- RSS-first for domains with known feeds.
- Google News RSS fallback for no-feed and unknown domains.

This routing is defined in `graph.py` via `DOMAIN_INGESTION_MAP`.

Examples:
- RSS: `openai.com`, `techcrunch.com`, `github.blog`, `langchain.com`, `news.ycombinator.com`, etc.
- Google News RSS fallback: `anthropic.com`, `a16z.com`, `tldr.tech`, `alphasignals.com`, `mybrandi.ai`, `uberall.com`, and any user-added unknown domain.
- VentureBeat now uses direct RSS: `https://venturebeat.com/category/ai/feed`.
- Direct RSS was also added for `searchengineland.com`, `searchenginejournal.com`, `marketingbrew.com`, `kopp-online-marketing.com`, and `semrush.com`.

## 3) File Structure (Core)

Root: `/home/toufic/Source/article-pipeline`

- `graph.py` - state schema, scout/analyst/approval/author nodes, routing, model selection.
- `settings.py` - env-backed config helpers.
- `main.py` - CLI start/resume flow.
- `ui.py` - FastAPI + HTML/JS UI.
- `preflight.py` - env and local service checks.
- `tests/test_graph_edges.py` - lightweight stdlib tests for draft review, URL safety, topic matching, and analyst prompt edges.
- `requirements.txt` - dependencies.
- `.env.example` - env template.
- `future-features.md`, `README.md`, `instructions.md`, `prd.md`, `spec.md`.

## 4) Data Models and Contracts

## 4.1 Python Models (`graph.py`)

`Article` (TypedDict):
- `id`, `title`, `url`, `source`, `published_at`, `summary`, `relevance_score`

`AgentState` (TypedDict, total=False):
- `raw_articles`, `curated_candidates`, `selected_article_id`, `final_draft`
- `workflow_status`, `human_feedback`, `scout_debug`
- runtime controls: `topic`, `include_domains`, providers/models

Note: `raw_articles` uses reducer `operator.add`.

Analyst structured schema:
- `AnalystPick(id, relevance_score)`
- `AnalystResponse(picks: List[AnalystPick])`

## 4.2 API Request Models (`ui.py`)

- `StartRequest`
- `ResumeRequest`
- `ProviderHealthRequest`

## 5) Scout Details

### 5.1 RSS path

Uses `feedparser` to parse feed entries.

Per-entry date extraction checks:
- parsed fields (`published_parsed`, `updated_parsed`, etc.)
- text fields (`published`, `updated`, `created`, `dc_date`)
- text scan fallback in title/summary/link

**Topic filtering (new)**:
- `_topic_keywords()` tokenizes the topic into significant keywords (lower-cased, stop-words removed, no minimum length gate so `"AI"`, `"ML"`, `"QA"` survive).
- `_topic_matches()` checks each keyword as a word-boundary regex (`\bkeyword\b`) against the entry’s title and summary.
- Entries that do not match are dropped with reason `topic_mismatch`.
- The topic is extracted from `state["topic"]` (UI textarea or CLI `--topic`).

Filters:
- drop missing title+url
- drop missing date when `ALLOW_UNDATED_ARTICLES=false`
- drop older than `MAX_ARTICLE_AGE_DAYS`
- **drop topic mismatch** (if topic keywords present)

## 5.2 Google News RSS fallback

Uses `_build_google_news_rss_url()` to generate a Google News RSS search URL for no-feed and unknown domains.

Query shape:
- `site:<domain>`
- `when:<MAX_ARTICLE_AGE_DAYS>d`

Google News fallback intentionally does **not** include topic terms in the remote query. Earlier topic-heavy queries produced `0 / 0` results because Google News over-constrained the search. Google News RSS entries now flow through the same `_normalize_rss_entries()` path as direct RSS, using `ingestion_mode="google_news"` for debug/progress labels.

URL enrichment safety:
- `_is_public_http_url()` is checked before `_http_fetch_text()`.
- Enrichment only fetches `http`/`https` URLs.
- Localhost, loopback, private, link-local, and other non-global IP targets are skipped.
- This is a small localhost hardening guard, not a general-purpose crawler security framework.

**Topic filtering (new)**:
- After retrieval, the same `_topic_matches(word-boundary)` check is applied client-side, dropping results that don't match the topic.
- This makes Google News fallback behavior match direct RSS filtering.

## 5.3 Volume Controls (new)

From `settings.py`:
- `RSS_MAX_ITEMS_PER_FEED` (default `25`)
- `SCOUT_MAX_TOTAL_ARTICLES` (default `80`)

Applied in scout:
- cap entries per feed
- dedupe URLs/titles
- sort by recency
- global cap to avoid huge candidate sets/cost spikes

## 6) Prompt Size Safeguards (new)

In `graph.py`:
- `ANALYST_MAX_ARTICLES = 20`
- `ANALYST_SUMMARY_MAX_CHARS = 260`
- `AUTHOR_SUMMARY_MAX_CHARS = 1600`

Analyst now receives a bounded, recency-prioritized subset to reduce provider 400 errors on message length.

### 6.1 Analyst topic instruction (dynamic)

The analyst LLM prompt no longer hardcodes MCP/agentic/SaaS themes. Instead it reads `state["topic"]` and injects:

```
Prioritize relevance to the user's chosen topic: {topic}.
Assume the reader is a technical founder or CTO evaluating strategic impact.
```

This keeps the persona framing while making the relevance target fully user-driven.

The recency sentence now uses configured `MAX_ARTICLE_AGE_DAYS` instead of a hardcoded `14` days:

```
Only choose articles that are clearly recent (published in the last {max_article_age_days} days).
```

### 5.4 Live source logging

During a scout run, `progress.py` maintains a `source_log` list on a per-thread basis. Each source (domain) is appended as it is queried, creating an ordered, real-time log of all ingestion calls.

The UI renders these as pills under the **Sources Queried** card, updated every 1.2 seconds via `/api/progress`.

---

## 6) Analyst Structured Output Parsing

The `_invoke_analyst_structured` function in `graph.py` has a two-layer retry:

1. **Primary path**: `analyst_llm.with_structured_output(AnalystResponse)` — the LangChain structured output path. If this succeeds, the response is returned directly.
2. **Fallback path**: If **any** exception occurs (ValidationError, NotImplementedError, etc.), the prompt is re-issued with an explicit JSON schema instruction:
   ```
   Return valid JSON only with this exact shape:
   {"picks": [{"id": "<id>", "relevance_score": 0.0}]}
   ```
   The raw LLM output is then passed through `_extract_json_payload`, which:
   - Strips markdown code fences
   - Tries `json.loads` directly
   - Falls back to extracting the first `{...}` block
   - If a JSON array `[...]` is found (common when the model omits the outer `picks` wrapper), it wraps it as `{"picks": [...]}`

This handles the common failure mode where the model returns a flat list of article objects instead of the expected nested shape.

---

## 7) Debug/Observability Shape

`scout_debug` now includes:
- `query`
- `include_domains`
- `effective_domain_mode = "domain_routing_map"`
- `routing`:
  - `rss_domains`
  - `google_news_domains`
  - `unknown_domains`
  - `fallback_domains`
- `stats`:
  - counts (`records_count`, `kept_count`, `dropped_count`)
  - recency settings
  - `max_total_articles`
  - `sample_urls`
  - `url_audit` entries with drop reasons and date-source details
- `sources` (per-feed/per-domain stats)
- `errors` (ingestion/parse/API issues)

## 8) UI State and UX

`ui.py` provides:
- Start / provider-health / resume / state / history endpoints
- Browser state with persistent domain selection (`localStorage`)
- **Sources Queried** card — live-updating pill display of each domain as the scout queries it (1.2s poll via `/api/progress`)
- `Dropped Articles Audit`
- `Domain diagnostics`
- Button busy states (`Starting...`, `Testing...`, etc.)
- Progress panel with step statuses:
  - Scout
  - Curate
  - Await Approval
  - Author Draft

### 8.0 Responsive layout

The UI is responsive across viewports. Recent CSS patches in `ui.py`:
- `.row` uses `flex-wrap: wrap` so domain/check rows reflow on narrow screens.
- Grids use `minmax(min(..., 100%), 1fr)` so single-column fallback kicks in cleanly on small viewports.
- Progress row uses `auto-fit` `minmax` so step indicators reflow.
- Cards, text, and JSON blocks use `min-width: 0` and `overflow-wrap: anywhere` to prevent overflow with long domain names or API responses.

### 8.0.1 Resume flow

The `/api/resume` endpoint is the single entry point used by both `Approve + Generate Draft` and `Edit`/`Publish`/`Done` actions. Behavior (`ui.py:1370`):
- If a `payload.action` is provided, it is forwarded verbatim (after merging `edited_draft` and `human_feedback`).
- If only `selected_article_id` is provided (the "Approve + Generate Draft" path), the server checks the current checkpoint's `next` nodes:
  - If `edit_approval` is in `next`, it first issues `Command(resume={"action": "pick_another"})` so the user re-enters the approval step.
  - It then issues the actual resume with `{"selected_article_id": ..., "human_feedback": ...}` to produce a fresh draft.
- The result is a deterministic "regenerate draft" path that always honors the current text in the article selector and human feedback textarea.

The button label in the UI is `Approve + Generate Draft` (`ui.py:450`); pressing it after `edit_approval` reopens the approval step and produces a new draft.

## 8.1 Draft Review Safety

Draft review action handling is centralized in `_apply_draft_review_action()` in `graph.py`.

Allowed actions:
- `publish`
- `edit`
- `pick_another`
- `done`

Important behavior:
- Unknown actions raise `ValueError`.
- `action="edit"` requires a non-empty `edited_draft`; otherwise it raises `ValueError`.
- Publishing only happens for explicit `action="publish"`.
- This prevents typo/empty-edit fallthrough from accidentally marking a draft as published.

## 9) Environment and Dependencies

Env keys in active use:
- `ARTICLE_PIPELINE_DEFAULT_TOPIC`
- `MAX_ARTICLE_AGE_DAYS`
- `ALLOW_UNDATED_ARTICLES`
- `RSS_MAX_ITEMS_PER_FEED`
- `SCOUT_MAX_TOTAL_ARTICLES`
- `NEWS_SOURCE_DOMAINS`
- provider keys/models (`OPENAI_*`, `GOOGLE_*`, `GROQ_*`, `OLLAMA_*`)
- `OLLAMA_API_KEY` added for cloud-hosted Ollama providers (e.g., `https://ollama.com`).
- `OLLAMA_MODEL_OPTIONS` (comma-separated list) now dynamically populates the UI model selection dropdown.

## 10) Deployment and Infrastructure

- **GitHub**: `https://github.com/guzziride/article-pipeline`
- **Docker**: 
  - `Dockerfile` (Python 3.12-slim)
  - `docker-compose.yml` (Handles persistence and environment)
  - Persistence: Local `drafts.db` volume-mounted to `/app/drafts.db`
- **Dependencies**:
  - Added `langchain-ollama` for modern Ollama integration.

## 10.1 Local Tests

Tests use Python stdlib `unittest`; no pytest dependency is required.

Current targeted test module:
- `tests/test_graph_edges.py` (10 tests)

Run:

```bash
# Local
.venv/bin/python -m unittest tests/test_graph_edges.py

# In container
docker compose exec -T article-pipeline python -m unittest tests/test_graph_edges.py
```

Coverage focus:
- empty edit does not publish
- unknown draft action does not publish
- explicit publish still publishes
- URL safety rejects local/private/non-HTTP targets
- acronym topic matching keeps `AI`/`ML`
- analyst prompt uses configured `MAX_ARTICLE_AGE_DAYS`
- Google News fallback URL builder uses only `site:` and `when:` and does not embed topic terms
- `edit_approval_node` rejects dict resumes without an explicit `action`

## 11) Current Known Issues / Open Items

- `MemorySaver` is still in-memory only; this is acceptable for localhost unless restart-survival becomes important.
- Scout still runs synchronously; acceptable for one local user, but slow scans could be optimized later with small bounded parallel fetches.
- `spec.md` and older planning docs may still describe older Tavily-first behavior and should be refreshed later if they become active references.
- No CI/linter/formatter is configured; current verification is manual local `unittest` plus CLI/UI runs.

## 12) Author Prompt Format

`author_node()` (`graph.py:1339`) builds the entire writer prompt as a single triple-quoted f-string (`graph.py:1365`). The prompt is intentionally a single editable block to make persona/voice tweaks easy. Substitutions:
- Persona block + voice rules + structure + style injection are all static text.
- Article fields (`title`, `url`, `published_at`, `summary`, `relevance_score`) are interpolated per article.
- `summary` is truncated to `AUTHOR_SUMMARY_MAX_CHARS` (1600) to avoid provider 400s.
- `human_feedback` is appended as a final `Human feedback: {feedback or 'None'}` line — there is no structural parsing, so any text the user enters is treated as free-form guidance.
- The model is invoked with `writer_llm.invoke(prompt)`; the `content` field of the response becomes `final_draft`.
