# Article Pipeline (LangGraph + HITL)

This project builds a LangGraph pipeline for technical-news curation and LinkedIn drafting:

1. **Scout** (RSS + Google News RSS fallback): finds current tech news and stores them in `raw_articles`.
2. **Analyst**: scores and curates top items into `curated_candidates`.
3. **Approval** (`interrupt()`): surfaces candidates and waits for your `selected_article_id`.
4. **Author**: drafts a polished LinkedIn post into `final_draft`.
5. **Edit approval** (`interrupt()`): publish, edit, pick another article, or finish.

It uses a `MemorySaver` checkpointer so you can stop at the interrupt and resume later with a selected article ID.
Scouting enforces recency with configurable settings:
- `MAX_ARTICLE_AGE_DAYS` (default `14`)
- `ALLOW_UNDATED_ARTICLES` (default `true`; set to `false` for strict date-only filtering)

## 1) Setup (Ubuntu / VS Code)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Environment Variables

Create `.env` in the project root.

```dotenv
ARTICLE_PIPELINE_DEFAULT_TOPIC=latest tech news on MCP, agentic workflows, and SaaS AI infrastructure
MAX_ARTICLE_AGE_DAYS=14
ALLOW_UNDATED_ARTICLES=true

# OpenAI
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o

# Google Gemini
GOOGLE_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash

# Groq
GROQ_API_KEY=...
GROQ_MODEL=llama-3.1-8b-instant

# Ollama (local)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

Notes:
- Scout uses RSS and Google News RSS fallback; no search API key is required.
- Gemini calls need `GOOGLE_API_KEY`.
- OpenAI calls need `OPENAI_API_KEY`.
- Groq calls need `GROQ_API_KEY`.
- Ollama calls need a local Ollama server running (`ollama serve`).

## 3) Run the Graph

### Start the graph (expected to interrupt)

```bash
python main.py --thread-id demo-1
```

To print a compact checkpoint snapshot after execution:

```bash
python main.py --thread-id demo-1 --show-state
```

What happens:
- Runs `scout` and `analyst`.
- Stops in `approval` via `interrupt()`.
- Prints shortlisted article IDs for resume.

### Resume with selected article

```bash
python main.py --thread-id demo-1 --selected-article-id 1 --human-feedback "Focus on SaaS GTM implications"
```

What happens:
- Reuses checkpoint state with the same `thread-id`.
- Resumes via `Command(resume={...})`.
- Executes `approval` continuation and then `author`.
- Prints `final_draft`.

## 4) Provider Toggles (Gemini/OpenAI/Groq/Ollama)

You can independently choose provider per node at runtime.
You can also override model names per node using `--analyst-model` and `--writer-model`.

Defaults:
- Analyst node: `gemini`
- Writer node: `openai`

Examples:

```bash
# Gemini analyst + OpenAI writer (default)
python main.py --thread-id demo-2 --analyst-provider gemini --writer-provider openai

# OpenAI analyst + OpenAI writer
python main.py --thread-id demo-3 --analyst-provider openai --writer-provider openai

# Groq analyst + Groq writer
python main.py --thread-id demo-groq --analyst-provider groq --writer-provider groq --analyst-model llama-3.1-8b-instant --writer-model llama-3.1-8b-instant

# Ollama analyst + OpenAI writer
python main.py --thread-id demo-4 --analyst-provider ollama --writer-provider openai

# Test both nodes on local Ollama before paid APIs
python main.py --thread-id demo-6 --analyst-provider ollama --writer-provider ollama --analyst-model llama3.1 --writer-model llama3.1

# Mix providers with explicit model overrides
python main.py --thread-id demo-7 --analyst-provider gemini --analyst-model gemini-2.0-flash --writer-provider openai --writer-model gpt-4o
```

If you want to use Gemini for writing too:

```bash
python main.py --thread-id demo-5 --writer-provider gemini
```

## 5) Files

- `graph.py`: graph definition, nodes, model routing, checkpointer, interrupt.
- `main.py`: runnable CLI showing start/interrupt/resume flow.
- `ui.py`: browser UI server for start/inspect/approve/resume HITL flow.
- `requirements.txt`: required Python packages.

## 6) Browser UI (start, inspect, approve, resume)

Run the web app:

```bash
uvicorn ui:web_app --reload --host 0.0.0.0 --port 3010
```

Open `http://localhost:3010` in your browser.

What you can do in UI:
- Start a flow with topic + providers + optional per-node model overrides.
- Run provider health checks (Gemini/OpenAI/Groq/Ollama) before starting a run.
- Edit source domains used by scouting with a pre-populated checklist.
- Uncheck any domain temporarily, and add new domains that persist in the browser for future runs.
- Inspect `curated_candidates` and current checkpoint state.
- Select an article ID from curated candidates or from the full raw scout list, then add optional human feedback.
- Copy the final draft with one click (markdown/plain-text compatible).
- Resume the graph and view `final_draft`.

CLI source filtering example:

```bash
python main.py --thread-id demo-8 --include-domains techcrunch.com,venturebeat.com,arstechnica.com
```

Optional env default for source filtering (`.env`):

```dotenv
NEWS_SOURCE_DOMAINS=techcrunch.com,venturebeat.com,arstechnica.com
```
