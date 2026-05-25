# Technical Specification: article-pipeline

## Tech Stack
- **Environment:** Ubuntu Linux, VS Code, OpenCode.
- **Orchestration:** LangGraph (Python).
- **Primary LLM (Analysis):** Google Gemini 2.0 Flash (via `langchain-google-genai`).
- **Secondary LLM (Creative):** OpenAI GPT-4o (via `langchain-openai`).
- **Local LLM (Summarization):** Ollama / Llama 3 (via `langchain-community`).
- **Search:** Tavily API.
- **Persistence:** LangGraph `MemorySaver`.

## Configuration
- `GOOGLE_API_KEY`: For Gemini analysis.
- `OPENAI_API_KEY`: For final LinkedIn drafting.
- `OLLAMA_BASE_URL`: For local summarization tasks.

## Graph Definition
- **State:** `TypedDict` containing `candidates` (List), `selected_id` (String), `draft` (String), `iteration_count` (Int).
- **Nodes:**
    1.  `discover_news`: Uses Tavily to find 5-7 high-tech articles.
    2.  `analyze_relevance`: Scores articles based on the User Persona in the PRD.
    3.  `human_approval`: An interrupt node that waits for a `selected_id`.
    4.  `draft_post`: Generates the LinkedIn content.
    5.  `refine_post`: A self-critique node that iterates if the tone is too "fluffy."

## Data Schema
```python
class AgentState(TypedDict):
    articles: List[dict]
    selection: str
    linkedin_draft: str
    critique: str