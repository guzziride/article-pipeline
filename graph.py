import os
import json
import re
import operator
import warnings
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse
from time import mktime
from typing import Any, Dict, List, Literal, Optional, TypedDict, Annotated, Union

from dotenv import load_dotenv
import feedparser
from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field
import progress as progress_tracker
from settings import (
    get_allow_undated_articles,
    get_default_topic,
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
    "anthropic.com": {"mode": "tavily", "endpoint": ""},
    "infoq.com": {"mode": "rss", "endpoint": "https://feed.infoq.com/"},
    "modelcontextprotocol.io": {"mode": "rss", "endpoint": "https://blog.modelcontextprotocol.io/index.xml"},
    "langchain.com": {"mode": "rss", "endpoint": "https://blog.langchain.dev/rss/"},
    "github.blog": {"mode": "rss", "endpoint": "https://github.blog/feed/"},
    "microsoft.com": {"mode": "rss", "endpoint": "https://www.microsoft.com/en-us/research/blog/feed/"},
    "a16z.com": {"mode": "tavily", "endpoint": ""},
    "stratechery.com": {"mode": "rss", "endpoint": "https://stratechery.com/feed/"},
    "theinformation.com": {"mode": "rss", "endpoint": "https://www.theinformation.com/feed"},
    "venturebeat.com": {"mode": "tavily", "endpoint": ""},
    "techcrunch.com": {"mode": "rss", "endpoint": "https://techcrunch.com/feed/"},
    "news.ycombinator.com": {"mode": "rss", "endpoint": "https://news.ycombinator.com/rss"},
    "tldr.tech": {"mode": "tavily", "endpoint": ""},
    "alphasignals.com": {"mode": "tavily", "endpoint": ""},
    "importai.substack.com": {"mode": "rss", "endpoint": "https://importai.substack.com/feed"},
    "latent.space": {"mode": "rss", "endpoint": "https://www.latent.space/feed"},
    "thelogic.co": {"mode": "rss", "endpoint": "https://thelogic.co/feed/"},
    "bctechassociation.org": {"mode": "rss", "endpoint": "https://wearebctech.com/feed/"},
}

ANALYST_MAX_ARTICLES = 20
ANALYST_MAX_PICKS = 20
ANALYST_SUMMARY_MAX_CHARS = 260
AUTHOR_SUMMARY_MAX_CHARS = 1600


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
        description="Relevance score for a CTO/PhD audience",
    )


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
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _invoke_analyst_structured(analyst_llm: Any, prompt: str) -> AnalystResponse:
    try:
        structured_llm = analyst_llm.with_structured_output(AnalystResponse)
        return structured_llm.invoke(prompt)
    except NotImplementedError:
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

    if provider == "gemini":
        model = chosen_model or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        return ChatGoogleGenerativeAI(model=model, temperature=0.2)

    if provider == "openai":
        model = chosen_model or os.getenv("OPENAI_MODEL", "gpt-4o")
        return ChatOpenAI(model=model, temperature=0.2)

    if provider == "groq":
        model = chosen_model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        return ChatGroq(model=model, temperature=0.2)

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
        )

    raise ValueError(f"Unsupported provider for {role}: {provider}")


def _default_include_domains() -> List[str]:
    raw = os.getenv("NEWS_SOURCE_DOMAINS", "")
    if not raw.strip():
        return []
    values = [part.strip().lower() for part in raw.split(",")]
    return [item for item in values if item]


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


def _extract_published_at(item: Dict[str, Any]) -> tuple[Optional[datetime], str, Dict[str, str]]:
    raw_date_fields: Dict[str, str] = {}
    date_keys = [
        "published_date",
        "published_at",
        "publishedAt",
        "date",
        "published",
        "pub_date",
        "created_at",
        "updated_at",
        "timestamp",
    ]

    for key in date_keys:
        candidate = item.get(key)
        if candidate is None:
            continue
        raw_date_fields[f"item.{key}"] = str(candidate)[:120]
        parsed = _parse_datetime(candidate)
        if parsed:
            return parsed.astimezone(timezone.utc), f"item.{key}", raw_date_fields

    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for key in date_keys:
            candidate = metadata.get(key)
            if candidate is None:
                continue
            raw_date_fields[f"metadata.{key}"] = str(candidate)[:120]
            parsed = _parse_datetime(candidate)
            if parsed:
                return parsed.astimezone(timezone.utc), f"metadata.{key}", raw_date_fields

    for field in ["title", "content", "url"]:
        value = str(item.get(field, "") or "")
        if not value.strip():
            continue
        parsed, matched_text = _extract_date_from_text(value)
        if parsed:
            if matched_text:
                raw_date_fields[f"{field}.matched_text"] = matched_text
            return parsed.astimezone(timezone.utc), f"{field}.text_scan", raw_date_fields

    return None, "", raw_date_fields


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


def _normalize_domain(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    no_scheme = re.sub(r"^https?://", "", raw)
    host = no_scheme.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _build_tavily_search_tool(max_results: int = 8):
    try:
        from langchain_tavily import TavilySearch

        return TavilySearch(
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
        ), "langchain_tavily"
    except Exception:
        from langchain_community.tools.tavily_search import TavilySearchResults

        warnings.filterwarnings(
            "ignore",
            category=DeprecationWarning,
            module=r"langchain_community\.tools\.tavily_search",
        )
        return TavilySearchResults(
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
        ), "langchain_community"


def _invoke_tavily_search(search_tool: Any, query: str, include_domains: Optional[List[str]] = None) -> Any:
    payload: Dict[str, Any] = {"query": query}
    if include_domains:
        payload["include_domains"] = include_domains

    attempts = [
        payload,
        {"query": query},
        query,
    ]
    last_error: Optional[Exception] = None
    for attempt in attempts:
        try:
            return search_tool.invoke(attempt)
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Tavily invocation failed for query '{query}': {last_error}")


def _resolve_domain_route(domain: str) -> Dict[str, str]:
    normalized = _normalize_domain(domain)
    explicit = DOMAIN_INGESTION_MAP.get(normalized)
    if explicit:
        return explicit
    return {"mode": "tavily", "endpoint": ""}


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
) -> tuple[List[Article], List[Dict[str, Any]], Dict[str, Any]]:
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
                    "ingestion_mode": "rss",
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
                    "ingestion_mode": "rss",
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
                    "ingestion_mode": "rss",
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
                "ingestion_mode": "rss",
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
        "ingestion_mode": "rss",
        "requested_domain": domain,
        "feed_url": feed_url,
        "entries_count": len(entries),
        "max_items_per_feed": max_items_per_feed,
        "kept_count": len(normalized),
        "dropped_no_title_url": dropped_no_title_url,
        "dropped_missing_date": dropped_missing_date,
        "dropped_old_date": dropped_old_date,
    }
    return normalized, audit, stats


def _http_fetch_text(url: str, timeout: float = 4.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ArticlePipelineBot/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = str(resp.headers.get("Content-Type", "")).lower()
        if "html" not in content_type and "xml" not in content_type:
            return ""
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read(250_000)
        return body.decode(charset, errors="replace")


def _find_first_date_in_jsonld(payload: Any) -> Optional[str]:
    candidate_keys = {"datePublished", "dateCreated", "dateModified", "uploadDate"}
    if isinstance(payload, dict):
        for key in candidate_keys:
            if key in payload:
                value = payload.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        for value in payload.values():
            found = _find_first_date_in_jsonld(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_date_in_jsonld(item)
            if found:
                return found
    return None


def _extract_date_from_html(html: str) -> tuple[Optional[datetime], str, str]:
    if not html:
        return None, "", ""

    meta_patterns = [
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|og:published_time|publish_date|pubdate|date|dc\.date|dc\.date\.issued)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:article:published_time|og:published_time|publish_date|pubdate|date|dc\.date|dc\.date\.issued)["\']',
    ]
    for pattern in meta_patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        parsed = _parse_datetime(value)
        if parsed:
            return parsed.astimezone(timezone.utc), "html.meta", value

    time_match = re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    if time_match:
        value = time_match.group(1).strip()
        parsed = _parse_datetime(value)
        if parsed:
            return parsed.astimezone(timezone.utc), "html.time", value

    jsonld_blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in jsonld_blocks:
        candidate = block.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        date_text = _find_first_date_in_jsonld(payload)
        if not date_text:
            continue
        parsed = _parse_datetime(date_text)
        if parsed:
            return parsed.astimezone(timezone.utc), "html.jsonld", date_text

    text_date, matched = _extract_date_from_text(html)
    if text_date:
        return text_date.astimezone(timezone.utc), "html.text_scan", matched

    return None, "", ""


def _enrich_published_at_from_url(url: str) -> tuple[Optional[datetime], str, str]:
    if not url:
        return None, "", ""

    try:
        html = _http_fetch_text(url)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None, "", ""
    except Exception:
        return None, "", ""

    return _extract_date_from_html(html)


def _normalize_tavily_results(
    raw: Any,
    include_domains: Optional[List[str]] = None,
    requested_domain: Optional[str] = None,
    query: str = "",
) -> tuple[List[Article], Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        results = raw.get("results")
        if isinstance(results, list):
            records = results

    normalized: List[Article] = []
    max_age_days = get_max_article_age_days()
    allow_undated = get_allow_undated_articles()
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - (max_age_days * 24 * 60 * 60)
    dropped_no_title_url = 0
    dropped_missing_date = 0
    dropped_old_date = 0
    url_enrichment_attempted = 0
    url_enrichment_success = 0
    requested_domains = sorted({(d or "").strip().lower() for d in (include_domains or []) if (d or "").strip()})
    returned_domain_counts: Dict[str, int] = {}
    kept_outside_requested_domains = 0
    url_audit: List[Dict[str, Any]] = []
    url_date_cache: Dict[str, tuple[Optional[datetime], str, str]] = {}
    for i, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        content = str(item.get("content", "")).strip()
        source = str(item.get("source", "")).strip()
        url_domain = _extract_domain(url)
        published_at, published_source, raw_date_fields = _extract_published_at(item)
        if not published_at and url:
            if url not in url_date_cache:
                url_enrichment_attempted += 1
                url_date_cache[url] = _enrich_published_at_from_url(url)
            enriched_at, enriched_source, enriched_raw = url_date_cache[url]
            if enriched_at:
                published_at = enriched_at
                published_source = f"url_enrichment.{enriched_source}" if enriched_source else "url_enrichment"
                raw_date_fields["url_enrichment.matched_value"] = enriched_raw[:120]
                url_enrichment_success += 1
        drop_reason = ""
        if not title and not url:
            dropped_no_title_url += 1
            drop_reason = "missing_title_and_url"
            url_audit.append(
                {
                    "id": str(i),
                    "title": title,
                    "url": url,
                    "published_at": published_at.isoformat() if published_at else "",
                    "url_domain": url_domain,
                    "published_at_source": published_source,
                    "raw_date_fields": raw_date_fields,
                    "kept": False,
                    "drop_reason": drop_reason,
                    "ingestion_mode": "tavily",
                    "requested_domain": requested_domain or "",
                    "query": query,
                }
            )
            continue
        if not published_at and not allow_undated:
            dropped_missing_date += 1
            drop_reason = "missing_publish_date"
            url_audit.append(
                {
                    "id": str(i),
                    "title": title,
                    "url": url,
                    "published_at": "",
                    "url_domain": url_domain,
                    "published_at_source": published_source,
                    "raw_date_fields": raw_date_fields,
                    "kept": False,
                    "drop_reason": drop_reason,
                    "ingestion_mode": "tavily",
                    "requested_domain": requested_domain or "",
                    "query": query,
                }
            )
            continue
        published_at_str = published_at.isoformat() if published_at else ""
        if published_at and published_at.timestamp() < cutoff:
            dropped_old_date += 1
            drop_reason = "older_than_max_age"
            url_audit.append(
                {
                    "id": str(i),
                    "title": title,
                    "url": url,
                    "published_at": published_at_str,
                    "url_domain": url_domain,
                    "published_at_source": published_source,
                    "raw_date_fields": raw_date_fields,
                    "kept": False,
                    "drop_reason": drop_reason,
                    "ingestion_mode": "tavily",
                    "requested_domain": requested_domain or "",
                    "query": query,
                }
            )
            continue
        url_audit.append(
            {
                "id": str(i),
                "title": title,
                "url": url,
                "published_at": published_at_str,
                "url_domain": url_domain,
                "published_at_source": published_source,
                "raw_date_fields": raw_date_fields,
                "kept": True,
                "drop_reason": "",
                "ingestion_mode": "tavily",
                "requested_domain": requested_domain or "",
                "query": query,
            }
        )
        if url_domain:
            returned_domain_counts[url_domain] = returned_domain_counts.get(url_domain, 0) + 1
            if requested_domains and url_domain not in requested_domains:
                kept_outside_requested_domains += 1
        normalized.append(
            {
                "id": str(i),
                "title": title or "Untitled",
                "url": url,
                "source": source or "unknown",
                "published_at": published_at_str,
                "summary": content,
                "relevance_score": 0.0,
            }
        )
    debug = {
        "records_count": len(records),
        "kept_count": len(normalized),
        "dropped_no_title_url": dropped_no_title_url,
        "dropped_missing_date": dropped_missing_date,
        "dropped_old_date": dropped_old_date,
        "url_enrichment_attempted": url_enrichment_attempted,
        "url_enrichment_success": url_enrichment_success,
        "requested_include_domains": requested_domains,
        "requested_domain": requested_domain or "",
        "query": query,
        "returned_domains_top": sorted(
            [{"domain": k, "count": v} for k, v in returned_domain_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:25],
        "kept_outside_requested_domains": kept_outside_requested_domains,
        "max_article_age_days": max_age_days,
        "allow_undated_articles": allow_undated,
        "generated_at_utc": now.isoformat(),
        "cutoff_utc": datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(),
        "sample_urls": [a.get("url", "") for a in normalized[:3]],
        "url_audit": url_audit,
    }
    return normalized, debug


def scout_node(state: AgentState) -> AgentState:
    topic = state.get(
        "topic",
        get_default_topic(),
    )
    include_domains = [_normalize_domain(d) for d in (state.get("include_domains") or _default_include_domains())]
    include_domains = [d for d in include_domains if d]

    max_age_days = get_max_article_age_days()
    base_query = f"{topic} from the last {max_age_days} days"

    rss_domains: List[Dict[str, str]] = []
    tavily_domains: List[str] = []
    unknown_domains: List[str] = []

    domain_targets = include_domains or sorted(DOMAIN_INGESTION_MAP.keys())
    for domain in domain_targets:
        route = _resolve_domain_route(domain)
        mode = route.get("mode", "tavily")
        endpoint = route.get("endpoint", "")
        if mode == "rss" and endpoint:
            rss_domains.append({"domain": domain, "feed_url": endpoint})
        elif mode == "tavily":
            tavily_domains.append(domain)
        else:
            unknown_domains.append(domain)

    all_articles: List[Article] = []
    all_audit: List[Dict[str, Any]] = []
    per_source_stats: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    next_id = 1

    tavily_available = bool(os.getenv("TAVILY_API_KEY", "").strip())
    tid = state.get("thread_id", "")
    total_sources = len(rss_domains) + (len(tavily_domains) if tavily_available else 0)
    if tid:
        progress_tracker.init_scout(tid, total_sources)
    completed = 0

    def _report(source_label: str) -> None:
        nonlocal completed
        completed += 1
        pct = int((completed / total_sources) * 80) if total_sources else 80
        if tid:
            progress_tracker.advance_scout(tid, source_label, pct, f"Scouting {source_label}... ({completed}/{total_sources})")

    for route in rss_domains:
        domain = route["domain"]
        feed_url = route["feed_url"]
        _report(domain)
        try:
            parsed_feed = feedparser.parse(feed_url)
            if getattr(parsed_feed, "bozo", False):
                errors.append(
                    {
                        "ingestion_mode": "rss",
                        "domain": domain,
                        "endpoint": feed_url,
                        "error": str(getattr(parsed_feed, "bozo_exception", "failed to parse feed")),
                    }
                )
                continue
            articles, audit, stats = _normalize_rss_entries(domain, feed_url, parsed_feed, start_id=next_id)
            all_articles.extend(articles)
            all_audit.extend(audit)
            per_source_stats.append(stats)
            next_id += len(audit)
        except Exception as exc:
            errors.append(
                {
                    "ingestion_mode": "rss",
                    "domain": domain,
                    "endpoint": feed_url,
                    "error": str(exc),
                }
            )

    if tavily_domains and not tavily_available:
        errors.append(
            {
                "ingestion_mode": "tavily",
                "domain": "*",
                "endpoint": "",
                "error": "Missing TAVILY_API_KEY; skipped Tavily-routed domains",
            }
        )

    if tavily_domains and tavily_available:
        search, search_impl = _build_tavily_search_tool(max_results=8)
        for domain in tavily_domains:
            _report(domain)
            domain_query = f"site:{domain} {base_query}"
            try:
                raw_results = _invoke_tavily_search(search, domain_query, include_domains=[domain])
                domain_articles, domain_debug = _normalize_tavily_results(
                    raw_results,
                    include_domains=[domain],
                    requested_domain=domain,
                    query=domain_query,
                )
                domain_debug["tavily_impl"] = search_impl
                all_articles.extend(domain_articles)
                all_audit.extend(domain_debug.get("url_audit", []))
                per_source_stats.append(domain_debug)
            except Exception as exc:
                errors.append(
                    {
                        "ingestion_mode": "tavily",
                        "domain": domain,
                        "endpoint": "",
                        "error": str(exc),
                    }
                )

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
            "tavily_domains": tavily_domains,
            "unknown_domains": unknown_domains,
            "fallback_domains": fallback_domains,
        },
        "stats": {
            "requested_domain_count": len(domain_targets),
            "rss_domain_count": len(rss_domains),
            "tavily_domain_count": len(tavily_domains),
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
            "You are a research analyst for a technical founder with a CTO + PhD profile.",
            "Prioritize relevance to these themes: Model Context Protocol (MCP), agentic workflows, and SaaS business impact.",
            "Only choose articles that are clearly recent (published in the last 14 days).",
            "Select up to the top 20 items. Prefer practical, strategic, and high-signal content over hype.",
            f"Input set has been truncated to the {len(prepared_articles)} most recent items for prompt size safety.",
            "Return only valid structured output.",
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

    prompt = "\n".join(
        [
            "Write a high-quality LinkedIn post for a CTO/PhD audience.",
            "Tone: authoritative, concise, practical, and intellectually honest.",
            "Structure:",
            "1) strong hook,",
            "2) why this matters now,",
            "3) 3 tactical insights,",
            "4) closing question for engagement.",
            "Keep it under 220 words. No emojis. No hashtags spam (max 3 relevant hashtags).",
            "",
            f"Article title: {article.get('title', '')}",
            f"Article url: {article.get('url', '')}",
            f"Article published_at: {article.get('published_at', '')}",
            f"Article summary: {(article.get('summary', '') or '')[:AUTHOR_SUMMARY_MAX_CHARS]}",
            f"Analyst relevance score: {article.get('relevance_score', 0.0)}",
            f"Human feedback: {feedback or 'None'}",
        ]
    )

    response = writer_llm.invoke(prompt)
    if tid:
        progress_tracker.set_phase(tid, "author", 100, "Draft generated")
    return {
        "final_draft": str(response.content),
        "workflow_status": "awaiting_draft_approval",
    }


def edit_approval_node(state: AgentState) -> AgentState:
    tid = state.get("thread_id", "")
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
        action = str(resume_data.get("action", "publish")).strip().lower()
        edited_draft = resume_data.get("edited_draft")
        if edited_draft is not None:
            edited_draft = str(edited_draft).strip()
    elif resume_data is not None:
        action = str(resume_data).strip().lower()

    result: Dict[str, Any] = {}
    if action == "done":
        result["workflow_status"] = "done"
    elif action == "pick_another":
        result["final_draft"] = None
        result["selected_article_id"] = None
        result["workflow_status"] = "awaiting_approval"
    elif action == "edit" and edited_draft:
        result["final_draft"] = edited_draft
        result["workflow_status"] = "awaiting_draft_approval"
    else:
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

    checkpointer = MemorySaver()
    app = workflow.compile(checkpointer=checkpointer)
    return app
