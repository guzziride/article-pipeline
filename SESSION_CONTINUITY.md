# Session Continuity

Use this file to resume quickly after context loss.

## Current Project State

- Project: `article-pipeline`
- Core workflow is active: `scout -> analyst -> approval(interrupt) -> author`
- Checkpointing: LangGraph `MemorySaver` (in-memory only)
- CLI + UI both support start/resume flows

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
   - Tavily for no-feed domains
5. Added dependencies for new scout stack:
   - `feedparser`
   - `langchain-tavily`
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

## Important Behavior Now

- Tavily is optional globally.
  - If only RSS-routed domains are selected, scout works without Tavily.
  - Tavily-routed domains need `TAVILY_API_KEY`.
- Scout debug now exposes routing, source-level stats, drop audits, and errors.

## Latest User-Reported Problem (Unresolved)

- User report at end of session: "I just ran a full scan and all units failed."
- This was not yet investigated before session end.

## Immediate Next Step For Next Session

Triage the "all units failed" run first.

Recommended sequence:
1. Reproduce with same thread and selected domains.
2. Inspect `scout_debug.errors` and per-source stats in UI state.
3. Run `/api/provider-health` checks for selected analyst/writer models.
4. Capture exact failure point (scout vs analyst vs author).
5. Fix root cause and re-run one full pass end-to-end.

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
