import os
import json
import re
import operator
import ipaddress
import socket
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse
from time import mktime
from typing import Any, Dict, List, Literal, Optional, TypedDict, Annotated, Union

from dotenv import load_dotenv
import feedparser
from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field
import progress as progress_tracker
import feed_cache
from settings import (
    get_allow_undated_articles,
    get_default_topic,
    get_factuality_check_enabled,
    get_max_article_age_days,
    get_rss_max_items_per_feed,
    get_scout_max_total_articles,
)


load_dotenv()


DOMAIN_INGESTION_MAP: Dict[str, Dict[str, str]] = {
    "arxiv.org": {"mode": "rss", "endpoint": "https://rss.arxiv.org/rss/cs"},
    "bair.berkeley.edu": {"mode": "rss", "endpoint": "https://bair.berkeley.edu/blog/feed/"},
    "deepmind.google": {"mode": "rss", "endpoint": "https://deepmind.google/blog/rss.xml"},
    "openai.com": {"mode": "rss", "endpoint": "https://openai.com/news/rss.xml"},
    "anthropic.com": {"mode": "google_news", "endpoint": ""},
    "infoq.com": {"mode": "rss", "endpoint": "https://feed.infoq.com/"},
    "modelcontextprotocol.io": {"mode": "rss", "endpoint": "https://blog.modelcontextprotocol.io/index.xml"},
    "langchain.com": {"mode": "rss", "endpoint": "https://blog.langchain.dev/rss/"},
    "github.blog": {"mode": "rss", "endpoint": "https://github.blog/feed/"},
    "microsoft.com": {"mode": "rss", "endpoint": "https://www.microsoft.com/en-us/research/blog/feed/"},
    "a16z.com": {"mode": "google_news", "endpoint": ""},
    "stratechery.com": {"mode": "rss", "endpoint": "https://stratechery.com/feed/"},
    "theinformation.com": {"mode": "rss", "endpoint": "https://www.theinformation.com/feed"},
    "venturebeat.com": {"mode": "rss", "endpoint": "https://venturebeat.com/category/ai/feed"},
    "techcrunch.com": {"mode": "rss", "endpoint": "https://techcrunch.com/feed/"},
    "news.ycombinator.com": {"mode": "rss", "endpoint": "https://news.ycombinator.com/rss"},
    "tldr.tech": {"mode": "google_news", "endpoint": ""},
    "alphasignals.com": {"mode": "google_news", "endpoint": ""},
    "importai.substack.com": {"mode": "rss", "endpoint": "https://importai.substack.com/feed"},
    "latent.space": {"mode": "rss", "endpoint": "https://www.latent.space/feed"},
    "thelogic.co": {"mode": "rss", "endpoint": "https://thelogic.co/feed/"},
    "bctechassociation.org": {"mode": "rss", "endpoint": "https://wearebctech.com/feed/"},
    "searchengineland.com": {"mode": "rss", "endpoint": "https://searchengineland.com/feed"},
    "searchenginejournal.com": {"mode": "rss", "endpoint": "https://www.searchenginejournal.com/feed/"},
    "marketingbrew.com": {"mode": "rss", "endpoint": "https://www.marketingbrew.com/feed.xml"},
    "kopp-online-marketing.com": {"mode": "rss", "endpoint": "https://www.kopp-online-marketing.com/feed/"},
    "semrush.com": {"mode": "rss", "endpoint": "https://www.semrush.com/blog/feed/"},
    "uberall.com": {"mode": "google_news", "endpoint": ""},
}

ANALYST_MAX_ARTICLES = 20
ANALYST_MAX_PICKS = 20
ANALYST_SUMMARY_MAX_CHARS = 260
AUTHOR_SUMMARY_MAX_CHARS = 1600
DRAFT_REVIEW_ACTIONS = {"publish", "edit", "pick_another", "done"}


class Article(TypedDict):
    id: str
    title: str
    url: str
    source: str
    published_at: str
    summary: str
    relevance_score: float


class PublishedDraft(TypedDict):
    published_at: str
    draft: str
    article_id: str


class AgentState(TypedDict, total=False):
    # `operator.add` acts as a reducer for `raw_articles`: each node update list is
    # appended to the existing checkpointed list instead of replacing it.
    # This is useful with checkpoints because resumed runs preserve and accumulate
    # discoveries from earlier steps.
    raw_articles: Annotated[List[Article], operator.add]
    curated_candidates: List[Article]
    selected_article_id: Optional[str]
    final_draft: Optional[str]
    workflow_status: str
    human_feedback: Optional[str]
    scout_debug: Dict[str, Any]

    # Runtime controls kept for provider toggles and topic customization.
    topic: str
    include_domains: List[str]
    analyst_provider: Literal["gemini", "openai", "ollama", "groq"]
    writer_provider: Literal["openai", "gemini", "ollama", "groq"]
    analyst_model: str
    writer_model: str

    # Runtime tracking
    thread_id: str

    # Draft review flow
    published_drafts: List[PublishedDraft]


class AnalystPick(BaseModel):
    id: Union[str, int] = Field(description="ID of an article from input list")
    relevance_score: float = Field(
        ge=0.0,
        le=10.0,
        description="Weighted relevance score (Contrarian Value 0.30, Technical Depth 0.25, Debate Potential 0.20, Timeliness 0.15, Source Credibility 0.10)",
    )
    contrarian_value: float = Field(ge=0.0, le=10.0, description="Does it challenge assumptions or reveal non-obvious truths?")
    technical_depth: float = Field(ge=0.0, le=10.0, description="Does it contain actionable implementation details, not just market commentary?")
    debate_potential: float = Field(ge=0.0, le=10.0, description="Would a post about this article generate meaningful discussion?")
    timeliness: float = Field(ge=0.0, le=10.0, description="Will this still be relevant in 3-5 days, or is it breaking news that fades fast?")
    source_credibility: float = Field(ge=0.0, le=10.0, description="Is this from a source known for technical rigor?")


class AnalystResponse(BaseModel):
    picks: List[AnalystPick] = Field(
        description=f"Top {ANALYST_MAX_PICKS} relevant articles for a CTO/PhD persona"
    )


def _extract_json_payload(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        # Try array-wrapping for list responses
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            array_data = json.loads(cleaned[start : end + 1])
            return {"picks": array_data}
        raise


def _invoke_analyst_structured(analyst_llm: Any, prompt: str) -> AnalystResponse:
    try:
        structured_llm = analyst_llm.with_structured_output(AnalystResponse)
        return structured_llm.invoke(prompt)
    except Exception:
        fallback_prompt = "\n\n".join(
            [
                prompt,
                "Return valid JSON only with this exact shape:",
                '{"picks": [{"id": "<id>", "relevance_score": 0.0}]}',
                "Do not include markdown or extra keys.",
            ]
        )
        response = analyst_llm.invoke(fallback_prompt)
        payload = _extract_json_payload(str(response.content))
        return AnalystResponse.model_validate(payload)


def _get_chat_model(provider: str, role: str, model_override: Optional[str] = None):
    provider = provider.lower().strip()
    chosen_model = (model_override or "").strip() or None
    request_timeout = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))
    max_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))

    if provider == "gemini":
        model = chosen_model or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        return ChatGoogleGenerativeAI(model=model, temperature=0.2, request_timeout=request_timeout, max_retries=max_retries)

    if provider == "openai":
        model = chosen_model or os.getenv("OPENAI_MODEL", "gpt-4o")
        return ChatOpenAI(model=model, temperature=0.2, request_timeout=request_timeout, max_retries=max_retries)

    if provider == "groq":
        model = chosen_model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        return ChatGroq(model=model, temperature=0.2, request_timeout=request_timeout, max_retries=max_retries)

    if provider == "ollama":
        model = chosen_model or os.getenv("OLLAMA_MODEL", "llama3.1")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        headers = {}
        api_key = os.getenv("OLLAMA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return ChatOllama(
            model=model,
            base_url=base_url,
            temperature=0.2,
            headers=headers if headers else None,
            timeout=request_timeout,
        )

    raise ValueError(f"Unsupported provider for {role}: {provider}")


def _default_include_domains() -> List[str]:
    raw = os.getenv("NEWS_SOURCE_DOMAINS", "")
    if not raw.strip():
        return []
    values = [part.strip().lower() for part in raw.split(",")]
    return [item for item in values if item]


def _topic_keywords(topic: str) -> List[str]:
    raw = (topic or "").strip()
    if not raw:
        return []
    text = raw.lower()
    for ch in ",.!?;:•–—()[]\"'":
        text = text.replace(ch, " ")
    stop_words = {
        "and", "the", "for", "with", "from", "has", "had", "was", "are",
        "that", "this", "have", "been", "their", "said", "each", "which",
        "how", "what", "now", "about", "get", "use", "all", "not", "but",
        "can", "will", "just", "also", "than", "only", "other", "some",
        "may", "its", "your", "one", "two", "three", "last", "first",
        "next", "year", "years", "day", "days", "week", "month", "time",
        "like", "know", "make", "way", "take", "come", "see", "go",
        "want", "think", "look", "find", "give", "tell", "ask", "work",
        "call", "try", "need", "feel", "seem", "turn", "hand", "put",
        "here", "there", "when", "where", "who", "why", "too", "very",
        "still", "well", "own", "out", "over", "again", "many", "much",
        "then", "them", "these", "those", "him", "her", "his", "he",
        "it", "we", "they", "our", "my", "me", "us", "a", "an", "on",
        "in", "at", "to", "of", "by", "up", "so", "as", "if", "or",
        "be", "is", "am", "do", "does", "did", "would", "should", "could",
    }
    tokens = []
    for word in text.split():
        w = word.strip()
        if len(w) <= 1:
            continue
        if w in stop_words:
            continue
        tokens.append(w)
    return tokens


def _topic_matches(keywords: List[str], title: str, summary: str) -> bool:
    if not keywords:
        return True
    hay = f"{title} {summary}".lower()
    return any(re.search(rf"\b{re.escape(kw)}\b", hay) for kw in keywords)


def _topic_search_terms(topic: str, max_terms: int = 14) -> List[str]:
    generic_words = {
        "act", "strategist", "scan", "news", "regarding", "last", "hours",
        "goal", "surface", "articles", "article", "high", "impact", "signal",
        "meaning", "discuss", "significant", "shifts", "prioritizing", "invite",
        "professional", "debate", "tech", "savvy", "from",
    }
    terms: List[str] = []
    seen: set[str] = set()
    for keyword in _topic_keywords(topic):
        if keyword in generic_words or keyword in seen:
            continue
        seen.add(keyword)
        terms.append(keyword)
        if len(terms) >= max_terms:
            break
    return terms


def _build_google_news_rss_url(domain: str, topic: str, max_age_days: int) -> str:
    normalized_domain = _normalize_domain(domain)
    query_parts = [f"site:{normalized_domain}", f"when:{max_age_days}d"]
    query = quote_plus(" ".join(query_parts))
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None

    raw = str(value).strip()
    if not raw:
        return None

    iso_candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_date_from_text(text: str) -> tuple[Optional[datetime], str]:
    if not text:
        return None, ""

    patterns = [
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
        r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b",
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}\s+\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{4}/\d{2}/\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(0)
        parsed = _parse_datetime(candidate)
        if parsed:
            return parsed.astimezone(timezone.utc), candidate

        cleaned = re.sub(r"\s+", " ", candidate).strip()
        for fmt in [
            "%B %d, %Y",
            "%b %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
            "%B %d %Y",
            "%b %d %Y",
            "%Y/%m/%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
        ]:
            try:
                return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc), candidate
            except ValueError:
                continue

    return None, ""


def _prepare_articles_for_analyst(articles: List[Article], max_items: int = ANALYST_MAX_ARTICLES) -> List[Article]:
    def _sort_key(article: Article) -> tuple[int, float]:
        raw_date = article.get("published_at", "")
        parsed = _parse_datetime(raw_date)
        if parsed:
            return (1, parsed.timestamp())
        return (0, 0.0)

    ranked = sorted(articles, key=_sort_key, reverse=True)
    selected: List[Article] = []
    for item in ranked[:max_items]:
        selected.append(
            {
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("source", "unknown"),
                "published_at": item.get("published_at", ""),
                "summary": (item.get("summary", "") or "")[:ANALYST_SUMMARY_MAX_CHARS],
                "relevance_score": float(item.get("relevance_score", 0.0) or 0.0),
            }
        )
    return selected


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_public_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False

    try:
        addresses = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, ValueError):
        return False

    for address in addresses:
        ip_text = address[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def _normalize_domain(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    no_scheme = re.sub(r"^https?://", "", raw)
    host = no_scheme.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _resolve_domain_route(domain: str) -> Dict[str, str]:
    normalized = _normalize_domain(domain)
    explicit = DOMAIN_INGESTION_MAP.get(normalized)
    if explicit:
        return explicit
    return {"mode": "google_news", "endpoint": ""}


def _datetime_from_struct_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(mktime(value), tz=timezone.utc)
    except Exception:
        return None


def _extract_feed_entry_date(entry: Any) -> tuple[Optional[datetime], str, Dict[str, str]]:
    raw_date_fields: Dict[str, str] = {}

    for key in ["published_parsed", "updated_parsed", "created_parsed"]:
        candidate = getattr(entry, key, None)
        parsed = _datetime_from_struct_time(candidate)
        if parsed:
            raw_date_fields[f"entry.{key}"] = str(candidate)
            return parsed, f"entry.{key}", raw_date_fields

    for key in ["published", "updated", "created", "dc_date"]:
        candidate = getattr(entry, key, None)
        if candidate is None:
            continue
        raw_date_fields[f"entry.{key}"] = str(candidate)[:120]
        parsed = _parse_datetime(candidate)
        if parsed:
            return parsed.astimezone(timezone.utc), f"entry.{key}", raw_date_fields

    title = str(getattr(entry, "title", "") or "")
    summary = str(getattr(entry, "summary", "") or "")
    link = str(getattr(entry, "link", "") or "")
    for field_name, text in [("title", title), ("summary", summary), ("link", link)]:
        parsed, matched = _extract_date_from_text(text)
        if parsed:
            raw_date_fields[f"{field_name}.matched_text"] = matched
            return parsed.astimezone(timezone.utc), f"{field_name}.text_scan", raw_date_fields

    return None, "", raw_date_fields


def _normalize_rss_entries(
    domain: str,
    feed_url: str,
    parsed_feed: Any,
    start_id: int,
    topic: str = "",
    ingestion_mode: str = "rss",
) -> tuple[List[Article], List[Dict[str, Any]], Dict[str, Any]]:
    keywords = _topic_keywords(topic)
    has_topic = bool(keywords)
    entries = getattr(parsed_feed, "entries", []) or []
    max_items_per_feed = get_rss_max_items_per_feed()
    entries = entries[:max_items_per_feed]
    normalized: List[Article] = []
    audit: List[Dict[str, Any]] = []

    max_age_days = get_max_article_age_days()
    allow_undated = get_allow_undated_articles()
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - (max_age_days * 24 * 60 * 60)

    dropped_missing_date = 0
    dropped_old_date = 0
    dropped_no_title_url = 0
    dropped_topic_mismatch = 0

    next_id = start_id
    for entry in entries:
        title = str(getattr(entry, "title", "") or "").strip()
        url = str(getattr(entry, "link", "") or "").strip()
        summary = str(getattr(entry, "summary", "") or "").strip()
        if not summary:
            summary = str(getattr(entry, "description", "") or "").strip()

        published_at, published_source, raw_date_fields = _extract_feed_entry_date(entry)
        drop_reason = ""

        if not title and not url:
            dropped_no_title_url += 1
            drop_reason = "missing_title_and_url"
            audit.append(
                {
                    "id": str(next_id),
                    "title": title,
                    "url": url,
                    "url_domain": _extract_domain(url),
                    "published_at": "",
                    "published_at_source": published_source,
                    "raw_date_fields": raw_date_fields,
                    "kept": False,
                    "drop_reason": drop_reason,
                    "ingestion_mode": ingestion_mode,
                    "feed_url": feed_url,
                    "requested_domain": domain,
                }
            )
            next_id += 1
            continue

        if not published_at and not allow_undated:
            dropped_missing_date += 1
            drop_reason = "missing_publish_date"
            audit.append(
                {
                    "id": str(next_id),
                    "title": title,
                    "url": url,
                    "url_domain": _extract_domain(url),
                    "published_at": "",
                    "published_at_source": published_source,
                    "raw_date_fields": raw_date_fields,
                    "kept": False,
                    "drop_reason": drop_reason,
                    "ingestion_mode": ingestion_mode,
                    "feed_url": feed_url,
                    "requested_domain": domain,
                }
            )
            next_id += 1
            continue

        published_at_str = published_at.isoformat() if published_at else ""
        if published_at and published_at.timestamp() < cutoff:
            dropped_old_date += 1
            drop_reason = "older_than_max_age"
            audit.append(
                {
                    "id": str(next_id),
                    "title": title,
                    "url": url,
                    "url_domain": _extract_domain(url),
                    "published_at": published_at_str,
                    "published_at_source": published_source,
                    "raw_date_fields": raw_date_fields,
                    "kept": False,
                    "drop_reason": drop_reason,
                    "ingestion_mode": ingestion_mode,
                    "feed_url": feed_url,
                    "requested_domain": domain,
                }
            )
            next_id += 1
            continue

        if has_topic and not _topic_matches(keywords, title, summary):
            dropped_topic_mismatch += 1
            drop_reason = "topic_mismatch"
            audit.append(
                {
                    "id": str(next_id),
                    "title": title,
                    "url": url,
                    "url_domain": _extract_domain(url),
                    "published_at": published_at_str,
                    "published_at_source": published_source,
                    "raw_date_fields": raw_date_fields,
                    "kept": False,
                    "drop_reason": drop_reason,
                    "ingestion_mode": ingestion_mode,
                    "feed_url": feed_url,
                    "requested_domain": domain,
                }
            )
            next_id += 1
            continue

        audit.append(
            {
                "id": str(next_id),
                "title": title,
                "url": url,
                "url_domain": _extract_domain(url),
                "published_at": published_at_str,
                "published_at_source": published_source,
                "raw_date_fields": raw_date_fields,
                "kept": True,
                "drop_reason": "",
                "ingestion_mode": ingestion_mode,
                "feed_url": feed_url,
                "requested_domain": domain,
            }
        )

        normalized.append(
            {
                "id": str(next_id),
                "title": title or "Untitled",
                "url": url,
                "source": _extract_domain(url) or domain or "unknown",
                "published_at": published_at_str,
                "summary": summary,
                "relevance_score": 0.0,
            }
        )
        next_id += 1

    stats = {
        "ingestion_mode": ingestion_mode,
        "requested_domain": domain,
        "feed_url": feed_url,
        "entries_count": len(entries),
        "max_items_per_feed": max_items_per_feed,
        "kept_count": len(normalized),
        "dropped_no_title_url": dropped_no_title_url,
        "dropped_missing_date": dropped_missing_date,
        "dropped_old_date": dropped_old_date,
        "dropped_topic_mismatch": dropped_topic_mismatch,
    }
    return normalized, audit, stats


def _fetch_feed_raw(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ArticlePipelineBot/1.0",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        # Some feeds (e.g. arxiv.org/rss/cs) run several MB; cap generously so we
        # bound worst-case memory/cache size without truncating real feeds mid-XML.
        body = resp.read(15_000_000)
        return body.decode(charset, errors="replace")


def scout_node(state: AgentState) -> AgentState:
    topic = state.get(
        "topic",
        get_default_topic(),
    )
    include_domains = [_normalize_domain(d) for d in (state.get("include_domains") or _default_include_domains())]
    include_domains = [d for d in include_domains if d]

    max_age_days = get_max_article_age_days()
    base_query = topic

    rss_domains: List[Dict[str, str]] = []
    google_news_domains: List[Dict[str, str]] = []
    unknown_domains: List[str] = []

    domain_targets = include_domains or sorted(DOMAIN_INGESTION_MAP.keys())
    for domain in domain_targets:
        route = _resolve_domain_route(domain)
        mode = route.get("mode", "google_news")
        endpoint = route.get("endpoint", "")
        if mode == "rss" and endpoint:
            rss_domains.append({"domain": domain, "feed_url": endpoint, "mode": "rss"})
        elif mode in {"google_news", "tavily"}:
            google_news_domains.append(
                {
                    "domain": domain,
                    "feed_url": _build_google_news_rss_url(domain, topic, max_age_days),
                    "mode": "google_news",
                }
            )
        else:
            unknown_domains.append(domain)

    all_articles: List[Article] = []
    all_audit: List[Dict[str, Any]] = []
    per_source_stats: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    next_id = 1

    tid = state.get("thread_id", "")
    source_routes = rss_domains + google_news_domains
    total_sources = len(source_routes)
    if tid:
        progress_tracker.init_scout(tid, total_sources)
    completed = 0
    _scout_lock = __import__("threading").Lock()

    def _report(source_label: str) -> None:
        nonlocal completed
        with _scout_lock:
            completed += 1
            pct = int((completed / total_sources) * 80) if total_sources else 80
        if tid:
            progress_tracker.advance_scout(tid, source_label, pct, f"Scouting {source_label}... ({completed}/{total_sources})")

    def _fetch_source(route: Dict[str, str]) -> tuple[List[Article], List[Dict[str, Any]], Dict[str, Any], List[Dict[str, str]], int]:
        domain = route["domain"]
        feed_url = route["feed_url"]
        mode = route.get("mode", "rss")
        _report(domain)
        try:
            cached = feed_cache.get(feed_url)
            if cached:
                parsed_feed = feedparser.parse(cached)
            else:
                raw_feed_text = _fetch_feed_raw(feed_url)
                parsed_feed = feedparser.parse(raw_feed_text)
                if not getattr(parsed_feed, "bozo", False):
                    try:
                        feed_cache.set(feed_url, raw_feed_text)
                    except Exception:
                        pass
            if getattr(parsed_feed, "bozo", False):
                return [], [], {}, [{
                    "ingestion_mode": mode,
                    "domain": domain,
                    "endpoint": feed_url,
                    "error": str(getattr(parsed_feed, "bozo_exception", "failed to parse feed")),
                }], 0
            articles, audit, stats = _normalize_rss_entries(
                domain,
                feed_url,
                parsed_feed,
                start_id=0,
                topic=topic,
                ingestion_mode=mode,
            )
            if tid:
                progress_tracker.record_source_result(
                    tid, domain, len(articles), stats.get("entries_count", 0), mode,
                    drops={k: stats.get(k, 0) for k in ["dropped_no_title_url", "dropped_missing_date", "dropped_old_date", "dropped_topic_mismatch"]},
                )
            return articles, audit, stats, [], len(audit)
        except Exception as exc:
            return [], [], {}, [{
                "ingestion_mode": mode,
                "domain": domain,
                "endpoint": feed_url,
                "error": str(exc),
            }], 0

    max_workers = min(int(os.getenv("SCOUT_MAX_WORKERS", "6")), total_sources) if total_sources else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_source, route): route for route in source_routes}
        for future in as_completed(futures):
            articles, audit, stats, errs, audit_count = future.result()
            all_articles.extend(articles)
            all_audit.extend(audit)
            if stats:
                per_source_stats.append(stats)
            errors.extend(errs)
            next_id += audit_count

    deduped_articles: List[Article] = []
    seen_urls: set[str] = set()
    for article in all_articles:
        url = (article.get("url") or "").strip()
        key = url.lower() if url else f"title::{(article.get('title') or '').strip().lower()}"
        if key in seen_urls:
            continue
        seen_urls.add(key)
        article["id"] = str(len(deduped_articles) + 1)
        deduped_articles.append(article)

    def _article_sort_key(article: Article) -> float:
        parsed = _parse_datetime(article.get("published_at", ""))
        return parsed.timestamp() if parsed else 0.0

    deduped_articles.sort(key=_article_sort_key, reverse=True)
    max_total_articles = get_scout_max_total_articles()
    if len(deduped_articles) > max_total_articles:
        deduped_articles = deduped_articles[:max_total_articles]
    for index, article in enumerate(deduped_articles, start=1):
        article["id"] = str(index)

    requested_domains = include_domains
    fallback_domains = [d for d in domain_targets if d not in DOMAIN_INGESTION_MAP]
    scout_debug: Dict[str, Any] = {
        "query": base_query,
        "include_domains": requested_domains,
        "effective_domain_mode": "domain_routing_map",
        "routing": {
            "rss_domains": rss_domains,
            "google_news_domains": google_news_domains,
            "tavily_domains": [],
            "unknown_domains": unknown_domains,
            "fallback_domains": fallback_domains,
        },
        "stats": {
            "requested_domain_count": len(domain_targets),
            "rss_domain_count": len(rss_domains),
            "google_news_domain_count": len(google_news_domains),
            "tavily_domain_count": 0,
            "unknown_domain_count": len(unknown_domains),
            "records_count": len(all_audit),
            "kept_count": len(deduped_articles),
            "dropped_count": len([item for item in all_audit if item.get("kept") is False]),
            "max_total_articles": max_total_articles,
            "max_article_age_days": get_max_article_age_days(),
            "allow_undated_articles": get_allow_undated_articles(),
            "sample_urls": [a.get("url", "") for a in deduped_articles[:3]],
            "url_audit": all_audit,
        },
        "sources": per_source_stats,
        "errors": errors,
    }

    if tid:
        progress_tracker.set_phase(tid, "scout", 80, f"Scout done: {len(deduped_articles)} kept, {len(errors)} errors")

    return {
        "raw_articles": deduped_articles,
        "include_domains": include_domains,
        "scout_debug": scout_debug,
        "workflow_status": "scouted" if deduped_articles else "no_recent_articles",
    }


def analyst_node(state: AgentState) -> AgentState:
    tid = state.get("thread_id", "")
    if tid:
        progress_tracker.set_phase(tid, "analyst", 85, "Curating top articles...")

    articles = state.get("raw_articles", [])
    if not articles:
        return {"curated_candidates": [], "workflow_status": "no_recent_articles"}

    prepared_articles = _prepare_articles_for_analyst(articles)

    if tid:
        progress_tracker.set_phase(tid, "analyst", 90, f"Running analyst LLM on {len(prepared_articles)} articles...")

    if not prepared_articles:
        return {"curated_candidates": [], "workflow_status": "no_recent_articles"}

    provider = state.get("analyst_provider", "ollama")
    analyst_model = state.get("analyst_model")
    analyst_llm = _get_chat_model(provider, role="analyst", model_override=analyst_model)

    topic = state.get("topic", "")
    max_article_age_days = get_max_article_age_days()

    article_lines = []
    for item in prepared_articles:
        article_lines.append(
            "\n".join(
                [
                    f"ID: {item.get('id', '')}",
                    f"Title: {item.get('title', '')}",
                    f"URL: {item.get('url', '')}",
                    f"Published at: {item.get('published_at', '')}",
                    f"Summary: {item.get('summary', '')[:ANALYST_SUMMARY_MAX_CHARS]}",
                ]
            )
        )

    prompt = "\n\n".join(
        [
            f"You are a research analyst curating articles for a technical founder (CTO + PhD). Topic: {topic}.",
            f"Only include articles published in the last {max_article_age_days} days. Return at most 20 picks.",
            "Only return articles with relevance_score >= 5.0. If none qualify, return an empty picks list.",
            "",
            "Score each article 0-10 on these axes, then compute relevance_score as the weighted average:",
            "  Contrarian/Insight Value (0.30): challenges assumptions, reveals non-obvious truths",
            "  Technical Depth (0.25): actionable implementation details, not market commentary",
            "  Debate Potential (0.20): would generate meaningful discussion among technical peers",
            "  Timeliness (0.15): still relevant in 3-5 days, not breaking news that fades fast",
            "  Source Credibility (0.10): known for technical rigor (engineering/research blogs > aggregators)",
            "",
            "AUTO-REJECT (score 0-2): press releases, funding announcements, generic trend pieces, clickbait, tutorials without insight, paywalled/thin summaries (<150 chars).",
            "STRONGLY PREFER (score 7-10): contrarian angles, tradeoff analysis, benchmarks/specific data, opinionated stances, articles that explain 'why this matters'.",
            "",
            "Example of a high-scoring article:",
            '  Title: "Why Agentic Workflows Break in Production"',
            "  Summary: A survey of 200 production agent deployments reveals 5 common failure modes including state corruption and unbounded retry loops. The authors propose a checkpoint-and-replay pattern that reduced incidents by 60%.",
            "  Scores: contrarian_value=9, technical_depth=8, debate_potential=9, timeliness=8, source_credibility=7, relevance_score=8.3",
            "",
            "Example of a low-scoring article:",
            '  Title: "Acme Corp Launches AI-Powered Dashboard"',
            "  Summary: Acme Corp announced a new AI-powered analytics dashboard for enterprise customers. The CEO said it will transform how businesses leverage data.",
            "  Scores: contrarian_value=1, technical_depth=1, debate_potential=1, timeliness=3, source_credibility=2, relevance_score=1.4",
            "",
            f"Input: {len(prepared_articles)} articles. Return only valid structured output.",
            "Articles:",
            "\n\n".join(article_lines),
        ]
    )

    response = _invoke_analyst_structured(analyst_llm, prompt)
    id_to_article = {item.get("id", ""): item for item in prepared_articles}

    curated: List[Article] = []
    for pick in response.picks:
        article = id_to_article.get(str(pick.id))
        if not article:
            continue
        enriched: Article = {
            "id": article.get("id", ""),
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "source": article.get("source", "unknown"),
            "published_at": article.get("published_at", ""),
            "summary": article.get("summary", ""),
            "relevance_score": float(pick.relevance_score),
        }
        curated.append(enriched)

    curated.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
    if tid:
        progress_tracker.set_phase(tid, "analyst", 100, f"Curated {len(curated)} articles")
    return {
        "curated_candidates": curated[:ANALYST_MAX_PICKS],
        "workflow_status": "awaiting_approval" if articles else "no_recent_articles",
    }


def _route_after_analyst(state: AgentState) -> str:
    if state.get("raw_articles"):
        return "approval"
    return "end"


def approval_node(state: AgentState) -> AgentState:
    candidates = state.get("curated_candidates", [])
    raw_articles = state.get("raw_articles", [])
    if not candidates and not raw_articles:
        return {"workflow_status": "no_recent_articles"}

    resume_data = interrupt(
        {
            "message": "Review curated_candidates and choose selected_article_id to continue. You may also provide an ID from raw_articles.",
            "curated_candidates": candidates,
            "raw_article_ids": [item.get("id") for item in raw_articles],
            "expected_resume": {
                "selected_article_id": "<id>",
                "human_feedback": "optional drafting preference",
            },
        }
    )

    selected_id: Optional[str] = None
    human_feedback: Optional[str] = None

    if isinstance(resume_data, dict):
        selected_id = str(resume_data.get("selected_article_id", "")).strip() or None
        feedback = resume_data.get("human_feedback")
        if feedback is not None:
            human_feedback = str(feedback).strip() or None
    elif resume_data is not None:
        selected_id = str(resume_data).strip() or None

    if not selected_id:
        raise ValueError(
            "Resume payload must include selected_article_id. "
            "Use Command(resume={'selected_article_id': '<id>'})."
        )

    valid_ids = {item.get("id") for item in candidates}
    valid_ids.update({item.get("id") for item in state.get("raw_articles", [])})
    if selected_id not in valid_ids:
        raise ValueError(f"selected_article_id '{selected_id}' not found in curated_candidates or raw_articles.")

    return {
        "selected_article_id": selected_id,
        "human_feedback": human_feedback,
        "workflow_status": "approved",
    }


def _verify_factuality(draft: str, article: Article, llm: Any) -> str:
    if not draft or not article:
        return ""
    summary = (article.get("summary", "") or "")[:AUTHOR_SUMMARY_MAX_CHARS]
    if not summary.strip():
        return ""
    prompt = f"""You are a fact-checker. Compare the LinkedIn draft below against the source article summary. List any factual claims in the draft that are NOT supported by the source summary. If all claims are supported, say "All claims verified." Be concise.

Source summary:
{summary}

Draft:
{draft}"""
    try:
        response = llm.invoke(prompt)
        return str(response.content).strip()
    except Exception:
        return ""


def author_node(state: AgentState) -> AgentState:
    tid = state.get("thread_id", "")
    if tid:
        progress_tracker.set_phase(tid, "author", 95, "Generating LinkedIn draft...")

    selected_id = str(state.get("selected_article_id", "")).strip()
    if not selected_id:
        raise ValueError("selected_article_id is missing. Run approval_node first.")

    article = next(
        (item for item in state.get("curated_candidates", []) if item.get("id") == selected_id),
        None,
    )
    if not article:
        article = next(
            (item for item in state.get("raw_articles", []) if item.get("id") == selected_id),
            None,
        )
    if not article:
        raise ValueError("Selected article not found in curated_candidates or raw_articles.")

    provider = state.get("writer_provider", "ollama")
    writer_model = state.get("writer_model")
    writer_llm = _get_chat_model(provider, role="writer", model_override=writer_model)
    feedback = state.get("human_feedback")

    prompt = f"""Write a LinkedIn post about the article below. You are a senior CTO and Ph.D. — a builder who has spent years in the trenches of distributed systems, agentic AI, and production infrastructure.

VOICE:
- Authoritative, blunt, intellectually honest. No corporate jargon (leverage, synergy, transformative, game-changer, paradigm shift).
- Dry, cynical humor rooted in technical frustration — understatements, not jokes. "This is a fantastic way to spend a weekend debugging race conditions."
- Skeptical optimism: acknowledge the innovation, then immediately surface the hidden cost or operational bottleneck.
- Short, punchy sentences. Start in the middle of the argument — no filler intros like "I'm excited" or "In today's AI landscape."
- If the article claims something is "easy" or "seamless," mock that claim with a dry observation about the inevitable edge cases.

STRUCTURE:
1. Hook — A contrarian statement, blunt observation, or hard-learned lesson. Never a greeting or preamble.
2. Tension — Why this matters for builders, not spectators. The hidden difficulty or unspoken implication.
3. 3 Tactical Insights — Bullet points. What this means for implementation, architecture, or engineering strategy.
4. Closing — A provocative, opinionated question that invites debate from technical peers.

CONSTRAINTS:
- Under 220 words. No emojis. Max 3 hashtags. No em dashes.

EXAMPLE OUTPUT:
The promise of agentic workflows is that they'll automate complex reasoning chains. The reality is that most production deployments are one state corruption bug away from a 3 AM incident.

What the paper actually found: 200 production agent deployments, 5 recurring failure modes. The top two — state corruption during retries and unbounded tool-calling loops — account for 73% of incidents. This isn't a maturity problem. It's an architecture problem.

What this means if you're building:
• Checkpoint-and-replay isn't optional — it's table stakes. If your agent can't resume from a known good state after a tool call fails, you're shipping a time bomb.
• Tool call budgets matter more than model quality. A 3-tool-call limit with good error handling beats unlimited calls with a better model.
• Observability for agents is fundamentally different from observability for services. You need to trace the reasoning graph, not just the request graph.

The authors propose a checkpoint-and-replay pattern that reduced incidents by 60%. The catch? It adds 15-20% latency per tool call. Worth it for anything customer-facing. Probably overkill for internal dashboards.

Are we over-indexing on model benchmarks and under-investing in the operational primitives that actually keep agents running in production?

Article title: {article.get('title', '')}
Article url: {article.get('url', '')}
Article published_at: {article.get('published_at', '')}
Article summary: {(article.get('summary', '') or '')[:AUTHOR_SUMMARY_MAX_CHARS]}
Analyst relevance score: {article.get('relevance_score', 0.0)}
Human feedback: {feedback or 'None'}"""

    if tid:
        progress_tracker.clear_stream(tid)
        progress_tracker.set_phase(tid, "author", 95, "Generating LinkedIn draft...")

    full_draft = ""
    try:
        for chunk in writer_llm.stream(prompt):
            token = str(getattr(chunk, "content", "") or "")
            if token:
                full_draft += token
                if tid:
                    progress_tracker.append_stream_token(tid, token)
    except Exception:
        full_draft = str(writer_llm.invoke(prompt).content)

    if tid:
        progress_tracker.mark_stream_done(tid)
        progress_tracker.set_phase(tid, "author", 100, "Draft generated")

    factuality_notes = (
        _verify_factuality(full_draft, article, writer_llm)
        if get_factuality_check_enabled()
        else ""
    )

    return {
        "final_draft": full_draft,
        "workflow_status": "awaiting_draft_approval",
        "scout_debug": {**(state.get("scout_debug") or {}), "factuality_notes": factuality_notes},
    }


def _apply_draft_review_action(
    state: AgentState,
    action: str,
    edited_draft: Optional[str] = None,
) -> AgentState:
    tid = state.get("thread_id", "")
    final_draft = state.get("final_draft")
    result: Dict[str, Any] = {}

    if action not in DRAFT_REVIEW_ACTIONS:
        raise ValueError(
            f"Unsupported draft review action '{action}'. "
            f"Expected one of: {', '.join(sorted(DRAFT_REVIEW_ACTIONS))}."
        )

    if action == "done":
        result["workflow_status"] = "done"
    elif action == "pick_another":
        result["final_draft"] = None
        result["selected_article_id"] = None
        result["workflow_status"] = "awaiting_approval"
    elif action == "edit":
        if not edited_draft:
            raise ValueError("edited_draft is required when action is 'edit'.")
        result["final_draft"] = edited_draft
        result["workflow_status"] = "awaiting_draft_approval"
    elif action == "publish":
        now = datetime.now(timezone.utc)
        existing_drafts = state.get("published_drafts", []) or []
        result["final_draft"] = None
        result["selected_article_id"] = None
        result["workflow_status"] = "published"
        result["published_drafts"] = existing_drafts + [
            {
                "published_at": now.isoformat(),
                "draft": final_draft or "",
                "article_id": str(state.get("selected_article_id", "")),
            }
        ]
        if tid:
            progress_tracker.set_phase(tid, "edit_approval", 100, "Draft published")
    return result


def edit_approval_node(state: AgentState) -> AgentState:
    final_draft = state.get("final_draft")
    published_drafts = state.get("published_drafts", [])
    last_published = published_drafts[-1] if published_drafts else None

    resume_data = interrupt(
        {
            "message": "Review and approve or edit the draft. Set action to 'publish' to finalize, 'edit' to provide a revised draft, 'pick_another' to select a different article, or 'done' to finish.",
            "final_draft": final_draft or "",
            "last_published": last_published,
            "expected_resume": {
                "action": "publish | edit | pick_another | done",
                "edited_draft": "<optional revised full draft if action=edit>",
            },
        }
    )

    action = "publish"
    edited_draft: Optional[str] = None

    if isinstance(resume_data, dict):
        if "action" not in resume_data:
            raise ValueError("Draft review resume payload must include action.")
        action = str(resume_data.get("action", "")).strip().lower()
        edited_draft = resume_data.get("edited_draft")
        if edited_draft is not None:
            edited_draft = str(edited_draft).strip()
    elif resume_data is not None:
        action = str(resume_data).strip().lower()

    return _apply_draft_review_action(state, action, edited_draft)


def _route_after_author(state: AgentState) -> str:
    status = state.get("workflow_status", "")
    if status == "awaiting_draft_approval" or status == "awaiting_approval" or status == "published":
        return "edit_approval"
    if state.get("final_draft"):
        return "edit_approval"
    return "end"


def _route_after_edit_approval(state: AgentState) -> str:
    status = state.get("workflow_status", "")
    if status == "done":
        return "end"
    if status == "awaiting_approval":
        return "approval"
    return "edit_approval"


def build_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("scout", scout_node)
    workflow.add_node("analyst", analyst_node)
    workflow.add_node("approval", approval_node)
    workflow.add_node("author", author_node)
    workflow.add_node("edit_approval", edit_approval_node)

    workflow.add_edge(START, "scout")
    workflow.add_edge("scout", "analyst")
    workflow.add_conditional_edges(
        "analyst",
        _route_after_analyst,
        {"approval": "approval", "end": END},
    )
    workflow.add_edge("approval", "author")
    workflow.add_conditional_edges(
        "author",
        _route_after_author,
        {"edit_approval": "edit_approval", "end": END},
    )
    workflow.add_conditional_edges(
        "edit_approval",
        _route_after_edit_approval,
        {"approval": "approval", "edit_approval": "edit_approval", "end": END},
    )

    db_path = os.getenv("CHECKPOINT_DB_PATH", os.path.join(os.path.dirname(__file__), "checkpoints.db"))
    # Connect directly rather than via SqliteSaver.from_conn_string()'s contextmanager:
    # that generator's underlying connection gets closed by garbage collection as soon
    # as build_graph() returns (nothing keeps the generator itself alive), which closed
    # the database before the first invoke() ever ran.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    app = workflow.compile(checkpointer=checkpointer)
    return app
