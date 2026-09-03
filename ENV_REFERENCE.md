# Environment Variables Reference

All variables are defined in `.env` (copy `.env.example` to start). Descriptions are grouped by section, matching the order in `.env.example`.

## Scouting & Content

| Variable | Default | Description |
|:---|:---|:---|
| `NEWS_SOURCE_DOMAINS` | *(empty)* | Comma-separated list of domains to seed the UI's domain list on first load. After that, domains are managed in the UI and persisted to `domains.db` (server-side) + browser `localStorage`. Optional — the UI is the primary domain management surface. |
| `ARTICLE_PIPELINE_DEFAULT_TOPIC` | `latest tech news on MCP, agentic workflows, and SaaS AI infrastructure` | The default topic string pre-filled in the UI's Topic field and used by the topic-matching filter during scouting. Word-boundary regex is applied against article title + summary. |
| `MAX_ARTICLE_AGE_DAYS` | `14` | Maximum age (in days) for articles to be kept. Older articles are dropped during scouting. Also drives the Google News RSS `when:` query parameter and the analyst prompt's recency wording. |
| `ALLOW_UNDATED_ARTICLES` | `true` | If `true`, articles with no publish date are kept (when other criteria pass). If `false`, undated articles are dropped. |
| `RSS_MAX_ITEMS_PER_FEED` | `25` | Maximum number of entries to normalize per RSS feed source. Prevents a single large feed from dominating the candidate pool. |
| `SCOUT_MAX_TOTAL_ARTICLES` | `80` | Hard cap on total articles kept after deduplication and sorting across all sources. Excess articles (oldest first) are trimmed. |
| `SCOUT_MAX_WORKERS` | `6` | Number of concurrent threads for fetching RSS feeds via `ThreadPoolExecutor`. Raise for more parallelism, lower for constrained environments. |

## LLM Settings

| Variable | Default | Description |
|:---|:---|:---|
| `LLM_REQUEST_TIMEOUT` | `60` | Timeout in seconds for each LLM API call. Also used as the base for retry backoff. |
| `LLM_MAX_RETRIES` | `2` | Maximum number of retry attempts for a failed LLM call before giving up. |

## Database Paths

| Variable | Default | Description |
|:---|:---|:---|
| `CHECKPOINT_DB_PATH` | `checkpoints.db` | Path to the SQLite file used by `SqliteSaver` for LangGraph checkpointing (thread state, resume across restarts). |
| `DOMAIN_STORE_DB_PATH` | `domains.db` | Path to the SQLite file for `domain_store.py` — the shared server-side domain enable/disable list used by both the UI and the scheduler. |
| `COST_TRACKER_DB_PATH` | `costs.db` | Path to the SQLite file for `cost_tracker.py` — per-LLM-call token usage and approximate USD cost logging. |
| `STYLE_PROFILE_DB_PATH` | `style_profile.db` | Path to the SQLite file for `style_profile.py` — persistent user style rules injected into author and refine prompts. |

## Author & Draft

| Variable | Default | Description |
|:---|:---|:---|
| `ENABLE_FACTUALITY_CHECK` | `true` | If `true`, runs an LLM-based factuality verification after the author generates a draft, surfacing any unverified claims in the UI. If `false`, the factuality check is skipped (saves one LLM call per draft). |
| `SSE_STREAM_TIMEOUT_SECONDS` | `240` | Timeout in seconds for the SSE draft-streaming endpoint (`/api/stream/{thread_id}`). Must exceed `LLM_REQUEST_TIMEOUT` (and its retries) or the stream will report "done" while the author/factuality calls are still running. |

## Provider: OpenAI

| Variable | Default | Description |
|:---|:---|:---|
| `OPENAI_API_KEY` | *(empty)* | API key for OpenAI. Required if using `openai` as the analyst or writer provider. |
| `OPENAI_MODEL` | `gpt-4o` | Default model name used when `openai` is selected and no per-run model override is specified in the UI. |

## Provider: Google (Gemini)

| Variable | Default | Description |
|:---|:---|:---|
| `GOOGLE_API_KEY` | *(empty)* | API key for Google AI (Gemini). Required if using `gemini` as the analyst or writer provider. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Default Gemini model. Updated from the now-retired `gemini-2.0-flash` (which returns 404). If you see a 404 from Gemini, check for another model retirement before assuming a config bug. |

## Provider: Groq

| Variable | Default | Description |
|:---|:---|:---|
| `GROQ_API_KEY` | *(empty)* | API key for Groq. Required if using `groq` as the analyst or writer provider. |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Default model name used when `groq` is selected and no per-run model override is specified. |

## Provider: Ollama

| Variable | Default | Description |
|:---|:---|:---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Base URL for the Ollama API. Point this at a cloud-hosted Ollama instance if not running locally. |
| `OLLAMA_MODEL` | `llama3.1` | Default model name used when `ollama` is selected and no per-run model override is specified. |
| `OLLAMA_API_KEY` | *(empty)* | API key for cloud-hosted Ollama providers. Leave empty for local Ollama (no auth). |
| `OLLAMA_MODEL_OPTIONS` | `gemini-3-flash-preview:cloud,gemma4:31b-cloud,llama3.1,llama3.2` | Comma-separated list of model names that populate the UI's model dropdown/datalist for Ollama. Used to offer quick-select options in the analyst/writer model fields. |
| `WRITER_TEMPERATURE` | `0.7` | Sampling temperature for creative roles (`writer`, `refine`, `learn_edit`). Analyst and health checks stay at `0.2` for deterministic ranking. |

## Scheduler

The scheduler runs scout + analyst unattended on a cron schedule, stops at the approval interrupt, stores candidates, and emails an HTML digest. Disabled by default.

| Variable | Default | Description |
|:---|:---|:---|
| `SCHEDULER_ENABLED` | `false` | Master toggle for the APScheduler `BackgroundScheduler`. Set to `true` to enable scheduled runs. |
| `SCHEDULER_CRON` | `0 7 * * 1-5` | Cron expression for the scheduled job (minute hour day month day-of-week). Default: 7:00 AM, Monday through Friday. |
| `SCHEDULER_TOPIC` | `agentic AI, MCP, SaaS infrastructure` | Topic string used for scheduled runs (separate from the UI's `ARTICLE_PIPELINE_DEFAULT_TOPIC`). |
| `SCHEDULER_DOMAINS` | `arxiv.org,openai.com,langchain.com,github.blog,techcrunch.com` | Fallback domain list for scheduled runs. **Only used if the UI has never synced a domain list to the server** (`domains.db`). Once the UI has been opened and domains saved, the scheduler reads from `domain_store.get_enabled_domains()` instead. |
| `SCHEDULER_ANALYST_PROVIDER` | `ollama` | LLM provider for the analyst node in scheduled runs. |
| `SCHEDULER_WRITER_PROVIDER` | `ollama` | LLM provider for the author node in scheduled runs. |
| `SCHEDULER_ANALYST_MODEL` | *(empty)* | Optional model override for the analyst in scheduled runs. If empty, uses the provider's default model. |
| `SCHEDULER_WRITER_MODEL` | *(empty)* | Optional model override for the author in scheduled runs. If empty, uses the provider's default model. |
| `SCHEDULER_PERSONA` | `cto_phd` | Persona used for draft generation in scheduled runs. One of `cto_phd`, `startup_founder`, `practitioner_engineer`. |
| `SCHEDULER_FORMAT` | `post` | Output format for draft generation in scheduled runs. One of `post`, `thread`, `carousel`. |

## Webhook Trigger

External trigger endpoint (`POST /api/webhook/trigger`) for unattended runs from services like Zapier or n8n. A webhook-triggered run goes through the same review flow as a scheduled run (Scheduled Runs panel + email digest). Disabled by default.

| Variable | Default | Description |
|:---|:---|:---|
| `WEBHOOK_SECRET` | *(empty)* | Shared secret for webhook authentication. **If unset/empty, the endpoint returns 501 (disabled).** When set, requests must include an `X-Webhook-Secret` header matching this value (compared with `secrets.compare_digest` — constant-time). |
| `WEBHOOK_TOPIC` | *(empty)* | Default topic for webhook-triggered runs if not provided in the request payload. Falls back to `SCHEDULER_TOPIC` if also empty. |
| `WEBHOOK_ANALYST_PROVIDER` | `ollama` | Default analyst provider for webhook runs if not in the payload. |
| `WEBHOOK_WRITER_PROVIDER` | `ollama` | Default writer provider for webhook runs if not in the payload. |
| `WEBHOOK_ANALYST_MODEL` | *(empty)* | Default analyst model override for webhook runs if not in the payload. |
| `WEBHOOK_WRITER_MODEL` | *(empty)* | Default writer model override for webhook runs if not in the payload. |
| `WEBHOOK_FORMAT` | *(empty)* | Default output format for webhook runs if not in the payload. Falls back to `post` if empty. |

## Email

SMTP settings for sending the scheduled-run digest email with candidate article links.

| Variable | Default | Description |
|:---|:---|:---|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname. |
| `SMTP_PORT` | `587` | SMTP server port. Use 587 for TLS, 465 for SSL. |
| `SMTP_USER` | *(empty)* | SMTP authentication username (typically your full email address). |
| `SMTP_PASSWORD` | *(empty)* | SMTP authentication password or app-specific password (e.g. a Gmail App Password, not your account password). |
| `EMAIL_FROM` | *(empty)* | Sender address for digest emails. |
| `EMAIL_TO` | *(empty)* | Recipient address for digest emails. |
| `BASE_URL` | `http://localhost:3010` | Base URL used to construct the "Review & Generate Draft" and "Skip This Batch" links in digest emails. Set to your public URL if accessing remotely. |