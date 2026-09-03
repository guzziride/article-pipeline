import os

from dotenv import load_dotenv


TOPIC_ENV_KEY = "ARTICLE_PIPELINE_DEFAULT_TOPIC"
TOPIC_FALLBACK = "latest tech news on MCP, agentic workflows, SaaS AI infrastructure, observability, and quantum computing"
MAX_ARTICLE_AGE_DAYS_KEY = "MAX_ARTICLE_AGE_DAYS"
MAX_ARTICLE_AGE_DAYS_FALLBACK = 14
ALLOW_UNDATED_ARTICLES_KEY = "ALLOW_UNDATED_ARTICLES"
ALLOW_UNDATED_ARTICLES_FALLBACK = True
RSS_MAX_ITEMS_PER_FEED_KEY = "RSS_MAX_ITEMS_PER_FEED"
RSS_MAX_ITEMS_PER_FEED_FALLBACK = 25
SCOUT_MAX_TOTAL_ARTICLES_KEY = "SCOUT_MAX_TOTAL_ARTICLES"
SCOUT_MAX_TOTAL_ARTICLES_FALLBACK = 80
OLLAMA_MODEL_OPTIONS_KEY = "OLLAMA_MODEL_OPTIONS"
OLLAMA_MODEL_OPTIONS_FALLBACK = "deepseek-v4-flash:cloud,gemma4:31b-cloud,llama3.1,llama3.2"
ENABLE_FACTUALITY_CHECK_KEY = "ENABLE_FACTUALITY_CHECK"
ENABLE_FACTUALITY_CHECK_FALLBACK = True
PAYWALLED_DOMAINS_KEY = "PAYWALLED_DOMAINS"
PAYWALLED_DOMAINS_FALLBACK = "theinformation.com,thelogic.co"
PAYWALL_MARKERS_KEY = "PAYWALL_MARKERS"
PAYWALL_MARKERS_FALLBACK = (
    "this post is for paid subscribers,for paid subscribers,subscriber-only,"
    "subscribers only,members only,member-only story,subscribe to continue reading,"
    "subscribe to read,create an account to continue reading,you have reached your article limit,"
    "to continue reading, subscribe,premium content,sign in to read,register to read"
)
PAYWALL_PROBE_KEY = "PAYWALL_PROBE"
PAYWALL_PROBE_FALLBACK = False
PAYWALL_PROBE_MAX_KEY = "PAYWALL_PROBE_MAX"
PAYWALL_PROBE_MAX_FALLBACK = 40
WRITER_EXAMPLES_KEY = "WRITER_EXAMPLES"
WRITER_EXAMPLES_FALLBACK = ""


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


def get_factuality_check_enabled() -> bool:
    load_dotenv(override=True)
    raw = (os.getenv(ENABLE_FACTUALITY_CHECK_KEY, str(ENABLE_FACTUALITY_CHECK_FALLBACK)) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_paywall_probe_enabled() -> bool:
    """If true, scout fetches each surviving article and drops paywalled ones."""
    load_dotenv(override=True)
    raw = (os.getenv(PAYWALL_PROBE_KEY, str(PAYWALL_PROBE_FALLBACK)) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_paywall_probe_max() -> int:
    """Maximum number of articles to probe for paywalls in a single scout run."""
    load_dotenv(override=True)
    raw = os.getenv(PAYWALL_PROBE_MAX_KEY, str(PAYWALL_PROBE_MAX_FALLBACK)) or ""
    try:
        value = int(raw)
    except ValueError:
        return PAYWALL_PROBE_MAX_FALLBACK
    return max(0, value)


def get_paywalled_domains() -> set[str]:
    """Genuinely all-paywall, non-technical domains. Scout drops any article
    originating from these before the analyst. Keep this small — never include
    mixed-platform domains like medium.com or substack.com."""
    load_dotenv(override=True)
    raw = os.getenv(PAYWALLED_DOMAINS_KEY, PAYWALLED_DOMAINS_FALLBACK) or ""
    domains: set[str] = set()
    for part in raw.split(","):
        value = (part or "").strip().lower()
        if value.startswith("www."):
            value = value[4:]
        if value:
            domains.add(value)
    return domains


def get_paywall_markers() -> list[str]:
    """Substrings to scan title+summary for at RSS level (no fetch). Default
    catches paid Substack/Medium posts and hard-paywall teasers."""
    load_dotenv(override=True)
    raw = os.getenv(PAYWALL_MARKERS_KEY, PAYWALL_MARKERS_FALLBACK) or ""
    return [m.strip().lower() for m in raw.split(",") if m.strip()]


def get_writer_examples() -> list[str]:
    """Few-shot examples of the user's actual writing, injected into the author
    prompt to match their voice instead of a generic AI voice. Set via
    WRITER_EXAMPLES env var (newline-separated) or a file path prefixed with
    'file:'."""
    import os as _os
    load_dotenv(override=True)
    raw = os.getenv(WRITER_EXAMPLES_KEY, WRITER_EXAMPLES_FALLBACK) or ""
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("file:"):
        path = raw[5:].strip()
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return []
    else:
        content = raw
    examples = [e.strip() for e in content.split("\n---\n") if e.strip()]
    return examples


DEFAULT_TOPIC = get_default_topic()
