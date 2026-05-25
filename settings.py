import os

from dotenv import load_dotenv


TOPIC_ENV_KEY = "ARTICLE_PIPELINE_DEFAULT_TOPIC"
TOPIC_FALLBACK = "latest tech news on MCP, agentic workflows, and SaaS AI infrastructure"
MAX_ARTICLE_AGE_DAYS_KEY = "MAX_ARTICLE_AGE_DAYS"
MAX_ARTICLE_AGE_DAYS_FALLBACK = 14
ALLOW_UNDATED_ARTICLES_KEY = "ALLOW_UNDATED_ARTICLES"
ALLOW_UNDATED_ARTICLES_FALLBACK = True
RSS_MAX_ITEMS_PER_FEED_KEY = "RSS_MAX_ITEMS_PER_FEED"
RSS_MAX_ITEMS_PER_FEED_FALLBACK = 25
SCOUT_MAX_TOTAL_ARTICLES_KEY = "SCOUT_MAX_TOTAL_ARTICLES"
SCOUT_MAX_TOTAL_ARTICLES_FALLBACK = 80
OLLAMA_MODEL_OPTIONS_KEY = "OLLAMA_MODEL_OPTIONS"
OLLAMA_MODEL_OPTIONS_FALLBACK = "gemini-3-flash-preview:cloud,gemma4:31b-cloud,llama3.1,llama3.2"


def get_default_topic() -> str:
    # Reload .env each call so UI reflects updates after restart/reload.
    load_dotenv(override=True)
    topic = os.getenv(TOPIC_ENV_KEY, TOPIC_FALLBACK)
    return (topic or "").strip() or TOPIC_FALLBACK


def get_max_article_age_days() -> int:
    load_dotenv(override=True)
    raw = (os.getenv(MAX_ARTICLE_AGE_DAYS_KEY, str(MAX_ARTICLE_AGE_DAYS_FALLBACK)) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return MAX_ARTICLE_AGE_DAYS_FALLBACK
    return max(1, value)


def get_allow_undated_articles() -> bool:
    load_dotenv(override=True)
    raw = (os.getenv(ALLOW_UNDATED_ARTICLES_KEY, str(ALLOW_UNDATED_ARTICLES_FALLBACK)) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_rss_max_items_per_feed() -> int:
    load_dotenv(override=True)
    raw = (os.getenv(RSS_MAX_ITEMS_PER_FEED_KEY, str(RSS_MAX_ITEMS_PER_FEED_FALLBACK)) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return RSS_MAX_ITEMS_PER_FEED_FALLBACK
    return max(1, value)


def get_scout_max_total_articles() -> int:
    load_dotenv(override=True)
    raw = (os.getenv(SCOUT_MAX_TOTAL_ARTICLES_KEY, str(SCOUT_MAX_TOTAL_ARTICLES_FALLBACK)) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return SCOUT_MAX_TOTAL_ARTICLES_FALLBACK
    return max(1, value)


def get_ollama_model_options() -> list[str]:
    load_dotenv(override=True)
    raw = os.getenv(OLLAMA_MODEL_OPTIONS_KEY, OLLAMA_MODEL_OPTIONS_FALLBACK)
    return [m.strip() for m in raw.split(",") if m.strip()]


DEFAULT_TOPIC = get_default_topic()
