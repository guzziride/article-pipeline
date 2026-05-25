# API Keys and Setup Instructions

This checklist covers everything needed to run the article pipeline end-to-end.

## 1) Core Project Setup (Ubuntu / VS Code)

Run from the project root:

```bash
cd /home/toufic/Source/article-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Verify key packages are installed:

```bash
python -m pip show langgraph langchain-openai langchain-google-genai tavily-python fastapi uvicorn
```

## 2) Create Environment File

```bash
cp .env.example .env
```

Populate `.env` with:

```dotenv
# Required for web/news search
TAVILY_API_KEY=...
ARTICLE_PIPELINE_DEFAULT_TOPIC=latest tech news on MCP, agentic workflows, and SaaS AI infrastructure
MAX_ARTICLE_AGE_DAYS=14
ALLOW_UNDATED_ARTICLES=true

# OpenAI
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o

# Gemini
GOOGLE_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash

# Groq
GROQ_API_KEY=...
GROQ_MODEL=llama-3.1-8b-instant

# Ollama (optional, local)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

## 3) API Keys Required

- `TAVILY_API_KEY`
  - Required for Scout node (`TavilySearchResults`)
  - Without it, graph start fails
- `GOOGLE_API_KEY`
  - Required when analyst or writer uses Gemini
  - Default Gemini model: `gemini-2.0-flash`
- `OPENAI_API_KEY`
  - Required when analyst or writer uses OpenAI
  - Default OpenAI model: `gpt-4o`
- `GROQ_API_KEY`
  - Required when analyst or writer uses Groq
  - Default Groq model: `llama-3.1-8b-instant`
- Ollama local runtime (no cloud key)
  - Required only if provider is `ollama`
  - Needs local server + pulled model

## 4) Provider Combination Requirements

- Default flow (Gemini analyst + OpenAI writer):
  - `TAVILY_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`
- Gemini + Gemini:
  - `TAVILY_API_KEY`, `GOOGLE_API_KEY`
- OpenAI + OpenAI:
  - `TAVILY_API_KEY`, `OPENAI_API_KEY`
- Ollama + OpenAI:
  - `TAVILY_API_KEY`, `OPENAI_API_KEY`, local Ollama running
- Ollama + Ollama:
  - `TAVILY_API_KEY`, local Ollama running

## 5) Ollama Setup (Optional)

If using `ollama` in any node:

```bash
ollama serve
ollama pull llama3.1
```

Ensure `.env` has:

- `OLLAMA_BASE_URL=http://localhost:11434`
- `OLLAMA_MODEL=llama3.1`

## 6) Non-Key Readiness Checks

- OpenAI model access/billing enabled for `gpt-4o`
- Gemini model access/billing enabled for `gemini-2.0-flash`
- Tavily key has active quota
- Outbound HTTPS access available (no firewall/proxy block)
- Run everything inside `.venv`
- Keep `.env` local; never commit secrets

## 7) Run Commands

CLI:

```bash
python main.py --thread-id demo-1 --show-state
python main.py --thread-id demo-1 --selected-article-id 1 --human-feedback "Focus on SaaS implications" --show-state
```

Browser UI:

```bash
uvicorn ui:web_app --reload --host 0.0.0.0 --port 3010
```

Open:

- `http://localhost:3010`

## 8) Common Issues

- `uvicorn: command not found`
  - venv not active or deps not installed in venv
- `Missing TAVILY_API_KEY`
  - `.env` missing, typo in variable name, or app started before env update
- OpenAI/Gemini auth errors
  - wrong key, no quota, or no model access
- Ollama errors
  - `ollama serve` not running, model not pulled, or wrong base URL
