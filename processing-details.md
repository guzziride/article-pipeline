# Processing Details: Article Pipeline

This file reflects the current implementation as of this session.

## 1) Purpose and Current Pipeline

`article-pipeline` is a LangGraph HITL workflow for technical-news discovery and LinkedIn drafting:

1. `scout` gathers recent articles.
2. `analyst` ranks top candidates.
3. `approval` interrupts for human selection.
4. `author` drafts the final post.

Graph topology in `graph.py`:
- `START -> scout -> analyst -> (approval or END) -> author -> END`
- `MemorySaver` checkpointing, resume by reusing `thread_id`.

## 2) Key Implementation Shift (Important)

Scout is no longer Tavily-only.

Current ingestion is **domain-routed dual strategy**:
- RSS-first for domains with known feeds.
- Tavily fallback only for domains mapped as `tavily` (or unknown domains).

This routing is defined in `graph.py` via `DOMAIN_INGESTION_MAP`.

Examples:
- RSS: `openai.com`, `techcrunch.com`, `github.blog`, `langchain.com`, `news.ycombinator.com`, etc.
- Tavily route: `anthropic.com`, `a16z.com`, `venturebeat.com`, `tldr.tech`, `alphasignals.com`.

## 3) File Structure (Core)

Root: `/home/toufic/Source/article-pipeline`

- `graph.py` - state schema, scout/analyst/approval/author nodes, routing, model selection.
- `settings.py` - env-backed config helpers.
- `main.py` - CLI start/resume flow.
- `ui.py` - FastAPI + HTML/JS UI.
- `preflight.py` - env and local service checks.
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

## 5.1 RSS path

Uses `feedparser` to parse feed entries.

Per-entry date extraction checks:
- parsed fields (`published_parsed`, `updated_parsed`, etc.)
- text fields (`published`, `updated`, `created`, `dc_date`)
- text scan fallback in title/summary/link

Filters:
- drop missing title+url
- drop missing date when `ALLOW_UNDATED_ARTICLES=false`
- drop older than `MAX_ARTICLE_AGE_DAYS`

## 5.2 Tavily path

Uses `langchain_tavily.TavilySearch` when available, with fallback to legacy community tool.

Date extraction for Tavily records:
- direct date fields
- metadata fields
- text scan
- optional URL-level HTML enrichment (`meta`, `time`, JSON-LD, text scan)

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

## 7) Debug/Observability Shape

`scout_debug` now includes:
- `query`
- `include_domains`
- `effective_domain_mode = "domain_routing_map"`
- `routing`:
  - `rss_domains`
  - `tavily_domains`
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
- `Dropped Articles Audit`
- `Domain diagnostics`
- Button busy states (`Starting...`, `Testing...`, etc.)
- Progress panel with step statuses:
  - Scout
  - Curate
  - Await Approval
  - Author Draft

## 9) Environment and Dependencies

Env keys in active use:
- `ARTICLE_PIPELINE_DEFAULT_TOPIC`
- `MAX_ARTICLE_AGE_DAYS`
- `ALLOW_UNDATED_ARTICLES`
- `RSS_MAX_ITEMS_PER_FEED`
- `SCOUT_MAX_TOTAL_ARTICLES`
- `NEWS_SOURCE_DOMAINS`
- provider keys/models (`OPENAI_*`, `GOOGLE_*`, `GROQ_*`, `OLLAMA_*`)
- `TAVILY_API_KEY` is now optional overall; required only for Tavily-routed domains.
- `OLLAMA_API_KEY` added for cloud-hosted Ollama providers (e.g., `https://ollama.com`).
- `OLLAMA_MODEL_OPTIONS` (comma-separated list) now dynamically populates the UI model selection dropdown.

## 10) Current Known Issues / Open Items


- Latest user report: "full scan and all units failed" (not yet triaged in this session).
- Need targeted failure capture from `scout_debug.errors`, provider health output, and runtime logs to isolate root cause.
- `spec.md` and parts of `README.md` may still describe older Tavily-first behavior and should be refreshed later.
