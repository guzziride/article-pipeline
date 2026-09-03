# Article Pipeline — User Manual

A personal, localhost tool that turns technical news into a polished LinkedIn draft. It scouts the web for articles matching your topic, has an AI analyst rank them, waits for **you** to pick one, then has an AI author write a draft in your chosen voice and format. You review, refine, and publish.

The whole flow is **human-in-the-loop**: the machine never publishes anything without your explicit approval.

---

## Table of Contents

1. [What the pipeline does](#1-what-the-pipeline-does)
2. [The five stages](#2-the-five-stages)
3. [Setup](#3-setup)
4. [Configuration (.env)](#4-configuration-env)
5. [Running the app](#5-running-the-app)
6. [Using the web UI](#6-using-the-web-ui)
7. [Using the CLI](#7-using-the-cli)
8. [Personas](#8-personas)
9. [Output formats](#9-output-formats)
10. [Style rules](#10-style-rules)
11. [Scheduled runs](#11-scheduled-runs)
12. [Webhook trigger](#12-webhook-trigger)
13. [Email digests](#13-email-digests)
14. [Performance dashboard](#14-performance-dashboard)
15. [Troubleshooting](#15-troubleshooting)

---

## 1) What the pipeline does

The app is a LangGraph workflow that runs in five stages. It finds recent technical news, curates it, and drafts a LinkedIn post — but stops at two points to ask for your input.

**Inputs you control:**
- A **topic** (what to look for)
- A set of **domains** (which sources to scan)
- An **analyst provider/model** (the AI that ranks articles)
- A **writer provider/model** (the AI that writes the draft)
- A **persona** (the voice of the draft)
- A **format** (single post, thread, or carousel)

**Outputs:**
- A ranked list of candidate articles
- A LinkedIn draft you can edit, refine, copy, or download
- A record of everything published, plus cost and performance stats

---

## 2) The five stages

| # | Stage | What happens | Stops for you? |
|---|-------|--------------|----------------|
| 1 | **Scout** | Fetches recent articles from your chosen domains via RSS (Google News RSS as fallback), deduplicates, filters by topic and age. | No |
| 2 | **Analyst** | An LLM ranks the top articles and picks the best candidates. | No |
| 3 | **Approval** | Shows you the curated candidates. | **Yes** — you pick an article |
| 4 | **Author** | An LLM writes a LinkedIn draft in your persona + format. | No |
| 5 | **Edit approval** | Shows you the draft. | **Yes** — publish, edit, pick another, or done |

Because the workflow uses a checkpointer, you can stop at either approval point and resume later — even after restarting the app.

---

## 3) Setup

### Prerequisites
- Python 3.10+
- At least one LLM provider configured (see [Configuration](#4-configuration-env))
- Optional: Docker (for containerized runs)

### Local install

```bash
# 1. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your environment file
cp .env.example .env
# ...then edit .env and fill in at least one provider's API key
```

### Docker

```bash
docker compose up --build -d
```

The app publishes on port `3010`.

---

## 4) Configuration (.env)

Copy `.env.example` to `.env` and fill in the values. A full reference with every variable is in `ENV_REFERENCE.md`. The essentials:

```dotenv
# Topic + scouting
ARTICLE_PIPELINE_DEFAULT_TOPIC=latest tech news on MCP, agentic workflows, and SaaS AI infrastructure
MAX_ARTICLE_AGE_DAYS=14
ALLOW_UNDATED_ARTICLES=true

# Pick at least one provider. Ollama is the default and needs no API key if local.
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1

# Optional providers
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o
GOOGLE_API_KEY=...
GEMINI_MODEL=gemini-3.6-flash
GROQ_API_KEY=...
GROQ_MODEL=llama-3.1-8b-instant
```

**Notes:**
- **Ollama** is the default for both analyst and writer. For a local install, run `ollama serve` and pull a model (`ollama pull llama3.1`). For cloud Ollama, set `OLLAMA_API_KEY` and point `OLLAMA_BASE_URL` at your cloud instance.
- **Scouting needs no search API key** — it uses RSS and Google News RSS.
- The scheduler, webhook, and email sections are optional; see their sections below.

---

## 5) Running the app

### Web UI (recommended)

```bash
uvicorn ui:web_app --reload --host 0.0.0.0 --port 3010
```

Open **http://localhost:3010** in your browser.

### CLI

```bash
# Start a run (stops at the approval step)
python main.py --thread-id test-1 --analyst-provider ollama --writer-provider ollama

# Resume after the approval interrupt, picking article 1
python main.py --thread-id test-1 --selected-article-id 1

# Full CLI flag list
python main.py --help
```

---

## 6) Using the web UI

The page is a single scrolling dashboard. Sections appear in order:

### 1) Start Flow
- **Thread ID** — the key for this run. Use a new one for a fresh run, or reuse one to resume.
- **Analyst / Writer provider** — choose the AI for ranking vs. writing (can differ).
- **Analyst / Writer model** — optional per-node model override.
- **Persona** — the voice of the draft (see [Personas](#8-personas)).
- **Format** — single post, thread, or carousel (see [Output formats](#9-output-formats)).
- **Topic** — what to look for.
- **Domains** — check/uncheck sources, or add a new domain. Your choices persist.
- **Start Graph** — begin the run.
- **Test Providers** — verify your provider keys/models work before starting.
- **Refresh State / Reset Thread** — reload the current state, or wipe this thread.

### 2) Articles Retrieved by Source
A live per-source breakdown of what scouting found, updating as the run progresses.

### 3) Curated Candidates (Approval Step)
The analyst's top picks. Enter the **Selected Article ID** of the one you want, optionally add **human feedback** (e.g. "emphasize SaaS pricing implications"), then click **Approve + Generate Draft**.

### 3b) Raw Articles (All Scout Results)
Collapsible list of every article scouting found — you can pick an ID from here too if the curated list misses something.

### 4) Draft Review & Publish
- **Quick Refine buttons** — Make Hook Punchier, Shorten, More Technical, Stronger CTA, Fix Grammar. One click re-runs the author with that instruction.
- **Draft editor** — a textarea you can edit directly.
- **Approve & Publish** — save the draft to your published history.
- **Save Edit** — save your manual edits as a new version.
- **Pick Another Article** — go back to the approval step and choose a different article.
- **Open LinkedIn Post** — open LinkedIn's composer in a new tab.
- **Copy Draft / Download .txt / Download .md** — export the draft.
- **Hashtag chips** — clickable hashtag suggestions.
- **Factuality notes** — if enabled, an LLM flags any unverified claims in the draft.
- **Cost summary** — token usage and approximate cost for this run.
- **Draft versions** — history of every draft/refine/edit for this thread.
- **Published Drafts (This Thread)** — what you've published.

### Sources Queried
A live log of every source the scout hit.

### Live State JSON
The raw graph state — useful for debugging.

### Performance Dashboard
See [Performance dashboard](#14-performance-dashboard).

### Scout Debug + Dropped Articles Audit
Diagnostics for why articles were excluded (e.g. `missing_publish_date`).

### Run History (Thread)
Start/resume/checkpoint snapshots for this thread.

### Scheduled Runs
Automated runs triggered by the scheduler. Click a run to load its candidates.

---

## 7) Using the CLI

The CLI mirrors the UI for scripted/headless use.

```bash
# Start (stops at approval)
python main.py --thread-id demo-1 --analyst-provider ollama --writer-provider ollama

# Resume and pick an article, with feedback
python main.py --thread-id demo-1 --selected-article-id 1 --human-feedback "Focus on SaaS GTM implications"

# Show a compact checkpoint snapshot
python main.py --thread-id demo-1 --show-state

# Filter sources
python main.py --thread-id demo-8 --include-domains techcrunch.com,venturebeat.com,arstechnica.com

# Mix providers with model overrides
python main.py --thread-id demo-7 --analyst-provider gemini --analyst-model gemini-3.6-flash --writer-provider openai --writer-model gpt-4o
```

After the draft is generated, the CLI supports the edit-approval actions:

```bash
python main.py --thread-id demo-1 --action publish
python main.py --thread-id demo-1 --action edit --edited-draft '...'
python main.py --thread-id demo-1 --action pick_another
python main.py --thread-id demo-1 --action done
```

---

## 8) Personas

The persona sets the **voice** of the draft. Three are built in:

| Persona | Label | Voice |
|--------|-------|-------|
| `cto_phd` | CTO / PhD (Technical Authority) | Authoritative, blunt, dry humor, skeptical optimism. Surfaces hidden costs and operational bottlenecks. |
| `startup_founder` | Startup Founder (Scrappy/Growth) | Energetic, direct, growth-obsessed, allergic to hype. Concrete numbers over theory. |
| `practitioner_engineer` | Practitioner Engineer (Hands-On) | First-person, grounded, "I tried this so you don't have to." Modest, plain language. |

Persona is orthogonal to format — persona = voice, format = shape. You pick one of each per run.

---

## 9) Output formats

The format sets the **shape** of the draft. Three are built in:

| Format | Label | Output |
|--------|-------|--------|
| `post` | Single Post | One LinkedIn post, under 220 words, max 3 hashtags. **Default** — byte-identical to the classic output. |
| `thread` | Thread (5-7 posts) | A numbered sequence of 5-7 short posts (each ≤280 chars), hook → body → close + CTA. |
| `carousel` | Carousel (6-8 slides) | Text for a 6-8 slide deck, formatted `[Slide N] Title / lines`, each line ≤~60 chars. |

The default format for scheduled and webhook runs is set by `SCHEDULER_FORMAT` and `WEBHOOK_FORMAT` in `.env`.

---

## 10) Style rules

Persistent, plain-text preferences that are injected into every draft and refine prompt as a `Standing style rules:` block. They layer on top of the persona.

- **Add a rule** in the Start Flow card's **Style Rules** panel (e.g. "keep posts under 200 words", "avoid exclamation marks").
- Rules are scoped to a **persona** (`*` = all personas) or a specific one.
- **Toggle / delete** rules from the same panel.
- **Promote-on-refine**: after any Quick Refine, a 6-second toast offers one-click "Save as style rule" — a convenient way to turn a one-off instruction into a permanent rule.
- Rules are **advisory** — the LLM may deviate. There is no automatic quality scoring; curation is deliberate and manual.

### Default rules

A starter set of rules ships with the repo. They are stored in `style_profile.db` (gitignored), so on a fresh clone run the seed script to restore them:

```bash
python seed_style_rules.py
# or in the container:
docker compose exec -T article-pipeline python seed_style_rules.py
```

The script is idempotent — it only adds rules that aren't already present. The defaults (all scoped to `*`):

- Keep the first sentence under 12 words; lead with a concrete fact, quote, or number, not a setup.
- No em-dashes at all. Use periods or commas instead.
- No buzzwords: never use delve, unlock, tapestry, robust, leverage, or game-changer.
- Include one first-person anecdote in parentheses for human voice.
- End with one specific debate question, not a summary sentence.
- Make it sound human, not like AI: add personalization, opinions, and rough edges.

Edit `DEFAULT_RULES` in `seed_style_rules.py` to change the shipped set.

---

## 11) Scheduled runs

The scheduler runs scout + analyst unattended on a cron schedule, stops at the approval interrupt, stores the candidates, and emails you a digest. **Disabled by default.**

In `.env`:

```dotenv
SCHEDULER_ENABLED=true
SCHEDULER_CRON=0 7 * * 1-5        # 7:00 AM, Mon-Fri
SCHEDULER_TOPIC=agentic AI, MCP, SaaS infrastructure
SCHEDULER_ANALYST_PROVIDER=ollama
SCHEDULER_WRITER_PROVIDER=ollama
SCHEDULER_PERSONA=cto_phd
SCHEDULER_FORMAT=post
```

- `SCHEDULER_DOMAINS` is only a **fallback** — once you've opened the UI and saved domains, the scheduler uses the same enabled-domain set you configured in the browser.
- Scheduled runs appear in the **Scheduled Runs** panel. Click one to load its candidates and continue the review flow.
- Requires the app to be running (the scheduler lives inside the web app process).

---

## 12) Webhook trigger

An external endpoint (`POST /api/webhook/trigger`) for triggering unattended runs from services like Zapier or n8n. A webhook run goes through the same review flow as a scheduled run.

- **Disabled by default** — it returns `501` unless `WEBHOOK_SECRET` is set.
- When enabled, requests must include an `X-Webhook-Secret` header matching `WEBHOOK_SECRET`.
- Configure defaults with `WEBHOOK_TOPIC`, `WEBHOOK_ANALYST_PROVIDER`, `WEBHOOK_WRITER_PROVIDER`, `WEBHOOK_ANALYST_MODEL`, `WEBHOOK_WRITER_MODEL`, `WEBHOOK_FORMAT`.

---

## 13) Email digests

Scheduled runs email you an HTML digest of the candidate articles with links to review and generate a draft. Configure in `.env`:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<Gmail App Password, not your account password>
EMAIL_FROM=you@gmail.com
EMAIL_TO=you@gmail.com
BASE_URL=http://localhost:3010
```

`BASE_URL` is used to build the "Review & Generate Draft" links in the email — set it to your public URL if accessing remotely.

---

## 14) Performance dashboard

A read-only aggregation of your usage, in the collapsible **Performance Dashboard** panel. Click **Refresh Dashboard** to load it (no auto-polling). Five sections:

- **Cost** — token usage and approximate cost, by node and by provider, daily.
- **Runs** — weekly rollup with email/review rates and average candidates per run.
- **Drafts** — published count, average length, and refine/author/manual-edit counts.
- **Topic Distribution** — which topics you've run most.
- **Style Rules** — which rules exist and how often each was applied.

---

## 15) Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| **No articles found** | Topic too narrow, or domains returning nothing. Check the **Scout Debug** and **Dropped Articles Audit** sections. |
| **Gemini 404 error** | The model was retired. Update `GEMINI_MODEL` to a current model (e.g. `gemini-3.6-flash`). |
| **Provider errors on start** | Run **Test Providers** in the UI to verify keys/models. |
| **Scheduled runs never fire** | `SCHEDULER_ENABLED` must be `true`, and the app must be running. |
| **Webhook returns 501** | `WEBHOOK_SECRET` is empty — the endpoint is intentionally disabled. |
| **No email digest** | SMTP settings incomplete, or `SMTP_PASSWORD` is your account password instead of an App Password. |
| **Draft stream reports done early** | `SSE_STREAM_TIMEOUT_SECONDS` is too low relative to `LLM_REQUEST_TIMEOUT` + retries. Raise it. |
| **Resume regenerates the draft** | By design — resuming with a `selected_article_id` always regenerates the draft. |

---

## Quick reference

```bash
# Run the web UI
uvicorn ui:web_app --reload --host 0.0.0.0 --port 3010

# Run via Docker
docker compose up --build -d

# Run the test suite
.venv/bin/python -m unittest discover -s tests -p "test_*.py"

# Preflight checks
python preflight.py --check-live
```
