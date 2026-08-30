# System, UX, & Feature Audit Report

## Executive Summary
- **Overall Quality Rating:** B+
- **Key Strengths:**
  - Clean, well-structured LangGraph HITL pipeline with clear node separation and fail-closed draft review safety.
  - Domain-routed ingestion (RSS + Google News RSS) eliminates dependency on paid search APIs and works without API keys.
  - Responsive, single-page UI with live progress tracking, per-source breakdowns, and persistent domain management via localStorage.
  - Strong defensive coding: URL enrichment guards, structured output fallback parsing, topic word-boundary matching, and 10 passing edge-case tests.
- **Critical Blockers/Gaps:**
  - No streaming or incremental output — scout runs fully synchronously, and LLM calls block the entire request. Users wait with no partial feedback during analyst/author phases.
  - In-memory checkpointing (`MemorySaver`) means all pipeline state is lost on server restart. No persistence for interrupted runs.
  - No inline draft refinement — users cannot ask the LLM to "make the hook punchier" or "shorten the CTA" without manually editing the full draft text.
  - No automated discovery or scheduling — every run must be manually triggered via UI or CLI.

---

## 1. Quality & Content Audit

### LLM Prompting Evaluation

**Strengths:**
- The author prompt (`graph.py:1365-1413`) is a single, well-organized triple-quoted f-string that defines persona, voice rules, structure, constraints, and style injection in one editable block. This is excellent for maintainability.
- The persona is specific and differentiated: "senior CTO and Ph.D. with deep technical experience," with concrete voice rules like "eliminate all corporate jargon" and "use dry, cynical, or gallows humor." This produces posts that are distinguishable from generic AI output.
- The structure template (Hook → Tension → 3 Tactical Insights → Closing Question) is a proven LinkedIn format.
- The analyst prompt dynamically injects the user's topic and configured `MAX_ARTICLE_AGE_DAYS`, avoiding hardcoded assumptions.
- Temperature is set to `0.2` for both analyst and author, which is appropriate for factual summarization and consistent tone.

**Weak Points:**
- The author prompt is entirely static aside from article fields and human feedback. There is no mechanism for the user to select a different persona (e.g., "Product Manager," "VC Investor," "Engineering Manager") or post format (e.g., "hot take," "bullet summary," "deep dive," "thread").
- Human feedback is appended as a raw string (`Human feedback: {feedback or 'None'}`) with no structural parsing. The LLM must interpret free-form text, which can produce inconsistent results. A user writing "make it shorter" may get a 10-word post or a 200-word post depending on the model's interpretation.
- The analyst prompt instructs the LLM to "Select up to the top 20 items" but the input is already capped at `ANALYST_MAX_ARTICLES=20`. This means the analyst is effectively asked to rank all provided articles, which is fine but the wording is misleading.
- No few-shot examples are provided in either prompt. Adding 1-2 example outputs would significantly improve consistency, especially for the structured analyst response format.

### Draft Quality & Engagement

**Strengths:**
- The voice rules explicitly ban corporate jargon ("leverage," "synergy," "tapestry," "transformative," "game-changer") and enforce short, punchy sentences. This directly combats the most common AI-slop failure mode.
- The "Reality Check Wit" instruction ("If an article claims a tool is 'easy' or 'seamless,' mock that claim") creates authentic, differentiated content that stands out on LinkedIn feeds.
- The 220-word limit and no-emoji constraint keep posts focused and professional.

**Weak Points:**
- There is no post-length variability. Some articles warrant a 50-word hot take; others deserve a 300-word deep dive. The hard 220-word cap may truncate valuable analysis for complex topics.
- Hashtag handling is minimal ("max 3, relevant to the technical topic"). The LLM must generate hashtags from scratch with no domain-specific hashtag library or trending-tag awareness.
- No support for multi-post threads. Many high-performing LinkedIn posts are 2-3 post threads that build a narrative arc.
- The closing question ("A provocative, opinionated question that invites debate") is a good pattern but can feel formulaic if every post ends with a question.

### Article Selection Quality & LinkedIn Curation Criteria

The current analyst prompt asks the LLM to rank articles by "relevance to the user's chosen topic" with a generic "prefer practical, strategic, and high-signal content over hype" instruction. This is too vague to consistently surface the best LinkedIn-worthy articles. The following criteria should be explicitly encoded into the analyst prompt and/or a pre-LLM heuristic filter.

**What makes an article excellent for LinkedIn posting (should score high):**

| Criterion | Why It Matters | Detection Heuristic |
|:---|:---|:---|
| **Contrarian or surprising angle** | Posts that challenge conventional wisdom generate the most engagement. "X is overrated" or "Why Y won't work" outperforms "X announced Y." | Title contains negation ("not," "won't," "stop," "never," "mistake"), question marks, or explicit counter-consensus framing. |
| **Actionable technical insight** | Readers share posts that teach them something concrete they can apply. Vague trend pieces get scrolled past. | Summary contains implementation details, architecture decisions, benchmarks, code patterns, or specific numbers/percentages. |
| **Decision or tradeoff, not announcement** | "We chose X over Y because Z" is infinitely more valuable than "We launched X." Tradeoffs reveal engineering judgment. | Summary contains comparative language ("vs," "instead of," "rather than," "tradeoff," "decision"). |
| **Has a "why this matters" layer** | The best LinkedIn posts add the poster's interpretation to the news. Articles that already include analysis give the LLM more to work with. | Summary goes beyond "what happened" to explain implications, second-order effects, or strategic context. |
| **Credible source with technical depth** | Posts referencing shallow content undermine the poster's credibility. Deep sources (research blogs, engineering blogs, academic papers) produce better drafts. | Source domain is a known engineering/research blog, not a general news aggregator. Summary length > 500 chars suggests depth. |
| **Timely but not ephemeral** | Articles with a shelf life of days/weeks (not hours) give the user time to review and post. Breaking news that will be stale in 4 hours is low value. | Published within 1-7 days (not hours). Topic is a trend or shift, not a single event. |
| **Invites debate or has an opinionated stance** | Posts that end with a question or take a clear position get more comments. Neutral summaries produce neutral engagement. | Summary or title contains strong opinion language, explicit claims, or calls to action. |
| **Specific data points or benchmarks** | Posts anchored in numbers ("37% faster," "2.3x cost reduction") are more credible and shareable than qualitative claims. | Summary contains percentages, benchmarks, dollar amounts, or comparative metrics. |

**What makes an article poor for LinkedIn posting (should score low or be filtered):**

| Anti-Pattern | Why It Fails | Detection Heuristic |
|:---|:---|:---|
| **Pure press release / product announcement** | "Company X launches Y" with no analysis. The LLM has nothing to add beyond paraphrasing the press release. | Title matches "announces," "launches," "introduces," "releases" with no analytical content in summary. |
| **Funding news without strategic angle** | "X raises $Y million" is commodity news. Unless the article explains *why* the round matters (market shift, technology bet), it's noise. | Title contains "raises," "funding," "Series" with no technical or strategic analysis in summary. |
| **Generic trend piece** | "AI is transforming industry Z" — no specifics, no data, no contrarian angle. These are AI-generated slop magnets. | Summary is under 200 chars, contains buzzwords without specifics, no numbers or named entities beyond the obvious. |
| **Tutorial / how-to without insight** | "How to deploy X on Y" is useful but not LinkedIn-post material unless it reveals a non-obvious gotcha or pattern. | Title starts with "How to" and summary is purely instructional with no broader implication. |
| **Commodity news everyone will post** | If 50 other people are posting about the same OpenAI announcement, your post needs a unique angle. Articles with no differentiation potential are low value. | Topic is a major vendor announcement covered by 10+ sources in the same scout run. |
| **Paywalled / thin-summary articles** | RSS feeds often provide only a 1-2 sentence teaser for paywalled content. The LLM has insufficient context to draft anything substantive. | Summary is under 150 chars and source domain is known paywalled (theinformation.com, thelogic.co, stratechery.com). |
| **Clickbait with no substance** | "You won't believe what happened" titles with no technical content. These waste the user's review time. | Title contains superlatives ("amazing," "incredible," "mind-blowing") with summary under 200 chars. |

**Recommended analyst prompt upgrade:**

The current analyst prompt should be extended with explicit scoring dimensions that map to the criteria above. Instead of a single 0-10 relevance score, the analyst should evaluate each article across multiple axes:

```
For each article, score 0-10 on these dimensions:
1. Contrarian/Insight Value: Does it challenge assumptions or reveal non-obvious truths?
2. Technical Depth: Does it contain actionable implementation details, not just market commentary?
3. Debate Potential: Would a post about this article generate meaningful discussion?
4. Timeliness Shelf-Life: Will this still be relevant in 3-5 days, or is it breaking news that fades fast?
5. Source Credibility: Is this from a source known for technical rigor?

Final relevance_score = weighted average (weights: 0.30, 0.25, 0.20, 0.15, 0.10)
```

This multi-axis scoring would produce more consistent curation than the current single-score approach and would give the user visibility into *why* an article was ranked highly.

### Factuality & Hallucination Risks

**Strengths:**
- The analyst prompt explicitly instructs the LLM to work from the provided article summaries, not from its own knowledge. The article URL, title, published date, and summary are all injected into the prompt.
- Summaries are truncated at `ANALYST_SUMMARY_MAX_CHARS=260` for the analyst and `AUTHOR_SUMMARY_MAX_CHARS=1600` for the author, which provides enough context for accurate drafting while staying within token limits.

**Weak Points:**
- There is **no factuality verification step**. The author LLM can hallucinate claims, statistics, or quotes that are not present in the source article. There is no post-generation check that cross-references the draft against the source summary.
- The analyst's relevance scoring has no calibration or consistency check. Two runs on the same articles may produce different scores and rankings.
- Source attribution in the final draft is implicit at best. The draft does not include a "Based on [Source] via [Publication]" line, which is standard practice for credible LinkedIn content curation.
- There is no mechanism to detect or flag when the source article's content was poorly extracted (e.g., paywalled article with only a 2-sentence RSS summary). The LLM will draft confidently from thin context.

---

## 2. UX / UI & Usability Audit

### Current User Journey

1. **Configure:** User sets thread ID, providers, topic, and domain checkboxes.
2. **Start:** User clicks "Start Graph." Scout runs synchronously (no streaming feedback during the 30-90s wait).
3. **Poll:** UI polls `/api/progress` every 1.2s to show source-by-source progress and a progress bar.
4. **Review Candidates:** After scout+analyst complete, curated candidates appear with relevance scores. Raw articles are available in a collapsible section.
5. **Select & Generate:** User enters an article ID, optionally adds human feedback, and clicks "Approve + Generate Draft."
6. **Review Draft:** Draft appears in an editable textarea. User can edit inline, publish, pick another article, or copy/open in LinkedIn.
7. **Publish:** Clicking "Approve & Publish" saves to SQLite and marks the workflow as published.

### Pain Points & Friction

- **No streaming during LLM calls:** The analyst and author phases are black boxes. The user sees a progress bar jump from 80% to 100% with no intermediate feedback. For slow LLM providers, this can be a 30+ second wait with no indication of activity.
- **Article selection is manual ID entry:** Users must type a numeric ID into a text field. A radio button or click-to-select interface on the candidate cards would be more intuitive. The "Use ID X" buttons on each card help but don't visually highlight the selection.
- **No inline AI refinement:** The draft editor is a plain textarea. Users cannot highlight text and ask "make this more technical" or "shorten this paragraph." All refinement must be done by manually editing text or re-running the entire generation with new human feedback.
- **Human feedback is a black box:** The feedback textarea has no guidance on what works well. Users don't know if "make it funnier" or "focus on security implications" will produce better results.
- **No undo/version history:** If a user edits the draft and wants to revert to the LLM's original output, there is no way to do so without regenerating.
- **Domain management is localStorage-only:** If a user switches browsers or clears localStorage, their custom domain list is lost. There is no server-side persistence or export/import.
- **No draft comparison view:** When regenerating a draft with different feedback, the user cannot compare the new draft side-by-side with the previous one.
- **The "Dropped Articles Audit" is technical:** It exposes raw date fields and internal drop reasons. While useful for debugging, it's noise for non-technical users.

### UI & Editing State Recommendations

- **Add streaming token output** for the author phase. Display draft text as it's generated (word-by-word or sentence-by-sentence) to eliminate the "waiting for a wall of text" experience.
- **Replace the article ID text input with a radio/selection UI.** Highlight the selected candidate card with a border/accent color.
- **Add inline AI actions:** A toolbar above the draft editor with buttons like "Make Hook Punchier," "Shorten," "More Technical," "Add Example," "Fix Grammar." Each sends a targeted re-prompt to the LLM with the current draft and the specific instruction.
- **Add a "Regenerate with different feedback" button** that preserves the current draft as a version and opens a side-by-side comparison.
- **Add toast notifications** for success/error states instead of `alert()` calls. The current `alert()` usage (e.g., "Draft copied to clipboard") is jarring.
- **Add a "Copy as Plain Text" option** alongside the existing "Copy Draft" to strip any markdown formatting for direct LinkedIn paste.
- **Persist domain configuration to the server** (or at minimum add an export/import button for the localStorage data).

---

## 3. Performance & Architecture Audit

### Search & Scraping Efficiency

**Strengths:**
- Domain-routed ingestion is efficient: known RSS feeds are fetched directly via `feedparser`, avoiding unnecessary Google News queries.
- `feedparser` is a well-established library that handles malformed feeds gracefully.
- Per-feed caps (`RSS_MAX_ITEMS_PER_FEED=25`) and global caps (`SCOUT_MAX_TOTAL_ARTICLES=80`) prevent unbounded growth.
- Deduplication by URL (and title fallback) prevents duplicate articles across sources.

**Weak Points:**
- **Scout is fully synchronous.** All RSS/Google News feeds are fetched sequentially in a `for` loop (`graph.py:1097`). With 30+ domains, this can take 30-90 seconds. There is no concurrency (e.g., `asyncio.gather` or `concurrent.futures.ThreadPoolExecutor`).
- **Google News RSS is slow and unreliable.** Google News RSS endpoints have inconsistent response times (2-8 seconds) and occasionally return empty results or CAPTCHA pages. There is no retry logic or timeout handling specific to Google News.
- **URL metadata enrichment fetches full HTML pages** (`_http_fetch_text` reads up to 250KB) for articles missing publish dates. This is a synchronous HTTP call per article and can add significant latency. There is no caching of enrichment results across runs.
- **No content extraction/readability parsing.** Article summaries come from RSS feed descriptions, which are often truncated or missing. There is no attempt to fetch and extract the full article body using a library like `readability-lxml` or `trafilatura` for richer summaries.
- **Paywalled content is silently degraded.** If an RSS feed only provides a 2-sentence teaser for a paywalled article, the analyst and author work with that thin context. There is no detection or flagging of low-quality summaries.

### Latency Analysis

| Phase | Estimated Time | Bottleneck |
|:---|:---|:---|
| Scout (30 domains) | 30-90s | Sequential HTTP fetches, no concurrency |
| Analyst (20 articles) | 5-20s | Single LLM call, no streaming |
| Author (1 article) | 5-20s | Single LLM call, no streaming |
| **Total** | **40-130s** | |

- The scout phase dominates latency. Parallelizing RSS fetches with a thread pool of 5-8 workers could reduce scout time to 5-15 seconds.
- LLM calls have no timeout configuration. A hung provider could block the pipeline indefinitely.
- There is no response caching. Re-running the same topic/domains within a short window re-fetches and re-analyzes everything.

### Error Resilience

**Strengths:**
- `feedparser` bozo detection catches malformed feeds and logs errors without crashing the pipeline (`graph.py:1104-1113`).
- Per-source try/except in the scout loop ensures one failing domain doesn't block others (`graph.py:1131-1139`).
- Structured output parsing has a two-layer fallback: `with_structured_output` → JSON re-prompt → `_extract_json_payload` with array wrapping (`graph.py:157-172`).
- Draft review is fail-closed: unknown actions and empty edits raise `ValueError` instead of silently publishing (`graph.py:1424-1465`).
- URL enrichment is guarded by `_is_public_http_url()` which rejects non-HTTP, localhost, private, loopback, and link-local targets (`graph.py:440-468`).

**Weak Points:**
- **No retry logic for transient failures.** If a Google News RSS endpoint returns a 503, the domain is silently skipped. A simple retry with exponential backoff (2-3 attempts) would improve yield.
- **No LLM call timeout.** `_get_chat_model` does not set `request_timeout` or `max_retries` on any provider. A hung Ollama instance or rate-limited cloud API will block the pipeline indefinitely.
- **No input validation on topic length.** A user could paste a 10,000-character topic, which would produce hundreds of keywords and potentially break the analyst prompt's token limit.
- **`MemorySaver` has no size limit.** Over many runs with large article sets, the in-memory checkpoint store could grow unbounded.
- **No graceful degradation for missing providers.** If the configured LLM provider is unreachable, the error surfaces as an HTTP 500 with a raw exception message. There is no pre-flight health check before starting a run.

### Database & State

**Strengths:**
- SQLite with WAL mode for published drafts provides reliable persistence with good concurrent read performance.
- Schema is simple and appropriate: `published_drafts` table with `thread_id`, `article_id`, `draft`, `published_at`.
- Draft store operations are thread-safe via a module-level `threading.Lock`.

**Weak Points:**
- **No persistence for pipeline state.** `MemorySaver` loses all checkpoints on restart. An interrupted approval or draft review is unrecoverable after a server restart.
- **No persistence for run history.** `RUN_HISTORY` in `ui.py` is an in-memory dict. All history is lost on restart.
- **No persistence for domain configuration.** Domains are in browser `localStorage` only.
- **No indexing on `published_drafts.article_id` or `published_drafts.published_at`.** Queries filtering by these fields will table-scan.
- **No draft versioning.** Each publish overwrites the `final_draft` in state. There is no history of draft iterations within a single article's workflow.

---

## 4. Automation & Pipeline Audit

### Automated Sourcing

**Current State:** All scouting is manually triggered via UI "Start Graph" button or CLI `main.py`. There is no scheduling, cron, or webhook-driven discovery.

**Recommendations:**
- Add a lightweight scheduler (e.g., `APScheduler` or a simple `asyncio` background task) that runs scout+analyst on a configurable interval (daily, every 6 hours, etc.) and stores candidates for later review.
- Add a "New Articles Since Last Check" view that shows only articles published after the user's last review session.
- Add webhook support so external systems (Zapier, n8n, custom scripts) can trigger a scout run via `/api/start`.

### Scheduled Runs with Email Digest

This is a high-value automation feature that eliminates the need to manually trigger scout runs and review results in the UI. The workflow:

1. **Scheduler triggers a scout+analyst run** on a configurable cron schedule (e.g., daily at 7 AM, every 6 hours on weekdays).
2. **Results are stored in SQLite** — curated candidates, raw articles, and scout debug data are persisted per scheduled run with a `run_id` and timestamp.
3. **An email digest is generated and sent** containing:
   - A subject line like "Article Pipeline: 5 candidates for Jul 26, 2026"
   - A ranked table of top curated candidates with title, source, relevance score, and a 1-line summary
   - Direct links to each source article
   - A "Review & Generate Draft" link that deep-links into the UI with the run pre-loaded
   - A "Skip This Batch" link to mark the run as reviewed without action
4. **Email delivery** via SMTP (configurable `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` in `.env`). For Gmail users, this works with an app-specific password. For production, a transactional email service (SendGrid, Mailgun, Resend) could be added.
5. **The UI gains a "Scheduled Runs" panel** showing past automated runs with their candidate lists, allowing the user to revisit any batch and generate drafts from it.

**Implementation approach:**

- Add `schedule.py` — a module that uses `APScheduler` (or a simple `asyncio` loop with `asyncio.sleep`) to trigger scout+analyst runs on a cron expression.
- Add `emailer.py` — a module that builds an HTML email from curated candidates and sends it via `smtplib` (stdlib, no new dependency) or an optional `resend`/`sendgrid` SDK.
- Extend `draft_store.py` with a `scheduled_runs` table: `run_id TEXT PK, thread_id TEXT, triggered_at TEXT, candidates_json TEXT, email_sent_at TEXT, reviewed_at TEXT`.
- Add a `/api/scheduler/status` endpoint to view/configure the schedule, and a `/api/scheduled-runs` endpoint to list past runs.
- The scheduler runs the same `build_graph()` pipeline with a dedicated `thread_id` (e.g., `scheduled-2026-07-26-0700`), waits for the `approval` interrupt, extracts the curated candidates from the checkpoint, stores them, and sends the email — all without human intervention. The interrupt is never resumed; the run is "paused" at approval until the user opens the UI and picks an article.

**Email digest example:**

```
Subject: Article Pipeline — 5 candidates for Jul 26, 2026

Top articles matching "agentic AI, MCP, and SaaS infrastructure":

1. [8.7] Why Agentic Workflows Break in Production
   arxiv.org — A survey of 200 production agent deployments reveals 5 common failure modes.
   → https://arxiv.org/abs/...

2. [8.2] The Hidden Cost of MCP Server Orchestration
   langchain.com — Latency and state management overhead in multi-server MCP setups.
   → https://blog.langchain.dev/...

3. [7.9] OpenAI Quietly Deprecates Assistants API — What It Means for SaaS
   techcrunch.com — The shift from managed agents to raw model access changes the build-vs-buy calculus.
   → https://techcrunch.com/...

4. [7.5] Benchmarking LLM Routing Strategies Across 6 Providers
   github.blog — Real-world latency and cost data for multi-model routing in production.
   → https://github.blog/...

5. [7.1] The VC Case for Vertical AI Agents Is Weaker Than It Looks
   a16z.com — Unit economics of vertical AI SaaS don't close at current inference costs.
   → https://a16z.com/...

Review & Generate Draft → http://localhost:3010/?run=scheduled-2026-07-26-0700
Skip This Batch → http://localhost:3010/api/scheduled-runs/skip/scheduled-2026-07-26-0700
```

**Configuration in `.env`:**

```bash
# Scheduler
SCHEDULER_ENABLED=true
SCHEDULER_CRON=0 7 * * 1-5        # 7 AM weekdays
SCHEDULER_TOPIC=agentic AI, MCP, SaaS infrastructure
SCHEDULER_DOMAINS=arxiv.org,openai.com,langchain.com,github.blog,techcrunch.com

# Email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com
EMAIL_TO=your-email@gmail.com
```

### Automated Scoring/Filtering

**Current State:** The analyst LLM scores articles 0-10 for relevance. There is no pre-LLM heuristic scoring to reduce the candidate set before the expensive LLM call.

**Recommendations:**
- Add a lightweight pre-filter based on keyword density, source reputation, and recency before sending articles to the analyst LLM. This would reduce LLM costs and latency.
- Add a "virality potential" heuristic based on title patterns (question titles, numbered lists, contrarian statements) that have historically performed well on LinkedIn.
- Store analyst scores per article and track which scored articles were ultimately selected by the user. Use this to calibrate future scoring.

### Export & Publishing Integration

**Current State:** The only export options are "Copy Draft" (clipboard) and "Open LinkedIn Post" (URL with pre-filled content). There is no API integration, no scheduling, and no multi-platform support.

**Recommendations:**
- Add LinkedIn API integration for direct posting (requires LinkedIn app registration and OAuth).
- Add a "Schedule Post" feature with a date/time picker that stores scheduled posts and publishes via LinkedIn API at the specified time.
- Add export formats: Markdown file download, plain text, HTML, and JSON.
- Add webhook notifications: POST the published draft to a configurable URL (e.g., to trigger a cross-post to Twitter/X, Slack, or a CMS).

---

## 5. Proposed Feature Enhancements & Value Additions

### Content Expansion

- **Multi-Post Thread Generator:** Add a "Generate Thread" option that produces a 3-5 post Twitter/LinkedIn thread from a single article, with each post building on the previous one.
- **Carousel Slide Outline:** Generate a 5-7 slide outline (title + bullet points per slide) suitable for LinkedIn carousel PDFs.
- **Short-Form Video Script:** Generate a 60-second video script (hook, key points, CTA) from the article for TikTok/Reels/Shorts adaptation.
- **Newsletter Summary:** Generate a 3-5 article briefing with a unified narrative for email newsletters.

### Personalized Memory & Style Tuning

- **Style Profile:** Allow users to define a persistent style profile (stored in SQLite or a config file) with their bio, tone preferences, frequently used terminology, and example posts they like. Inject this profile into every author prompt.
- **Past Performance Injection:** Store published posts and track which ones the user approved without edits vs. heavily edited. Use this to weight future prompt instructions (e.g., "You tend to prefer shorter hooks and more technical depth").
- **Domain-Specific Knowledge Base:** Allow users to upload a "knowledge file" (e.g., their company's product descriptions, technical whitepapers, or personal blog posts) that the LLM can reference for accurate technical claims.

### Analytics & Feedback Loop

- **Post Performance Tracking:** If LinkedIn API integration is added, fetch post impressions, engagements, and comments. Correlate performance with prompt parameters, article sources, and topics.
- **A/B Prompt Testing:** Allow users to define two prompt variants and generate drafts with both. Track which variant produces posts the user publishes more often.
- **Topic Performance Dashboard:** Show which topics and sources consistently produce the highest-scored and most-published articles.

### Scheduled Discovery & Email Digest

- **Cron-Driven Scout + Analyst Runs:** A background scheduler (`APScheduler` or `asyncio` loop) triggers the full scout→analyst pipeline on a configurable cron expression (e.g., daily at 7 AM weekdays). Results are stored in SQLite with a `run_id` and timestamp. The pipeline runs to the `approval` interrupt and stops — no human intervention needed.
- **Email Digest with Curated Candidates:** After each scheduled run, an HTML email is generated and sent via SMTP containing a ranked table of top candidates (title, source, relevance score, 1-line summary, direct article link). The email includes a "Review & Generate Draft" deep-link into the UI and a "Skip This Batch" link.
- **Scheduled Runs Panel in UI:** A new UI section lists past automated runs with their candidate counts and timestamps. Clicking a run loads its candidates into the approval step, allowing the user to generate drafts from any historical batch.
- **Configurable Schedule & Recipients:** Schedule (cron expression), topic, domains, and email recipients are all configurable via `.env`. Multiple email recipients supported (comma-separated `EMAIL_TO`).

### Contextual Knowledge & Memory

- **Persistent User Memory:** Store key facts about the user (company, role, expertise areas, opinions on specific technologies) and inject them into prompts as "About the Author" context.
- **Article Relationship Graph:** Track which articles the user has already drafted about to avoid redundant coverage and to enable "follow-up" post suggestions (e.g., "You posted about OpenAI's release last week — here's an update").

---

## 6. Prioritized Action Plan & Roadmap

| Priority | Category | Issue / Feature Improvement | Recommended Implementation | Impact |
| :--- | :--- | :--- | :--- | :--- |
| **P0 (Critical)** | Performance | Scout runs synchronously — 30-90s wait with no partial feedback | Parallelize RSS/Google News fetches with `concurrent.futures.ThreadPoolExecutor` (5-8 workers). Add per-source timeouts. | High |
| **P0 (Critical)** | Reliability | `MemorySaver` loses all state on restart | Replace with `SqliteSaver` or `AsyncSqliteSaver` from `langgraph.checkpoint.sqlite`. Add migration for existing in-memory usage. | High |
| **P0 (Critical)** | UX | No streaming during LLM calls — users stare at a progress bar | Use LangGraph's `stream()` or `astream_events()` for the author node. Stream tokens to the UI via SSE (Server-Sent Events) or WebSocket. | High |
| **P1 (High)** | UX | Article selection requires manual ID entry | Replace text input with radio buttons on candidate cards. Highlight selected card. Auto-fill ID on card click. | Medium-High |
| **P1 (High)** | Content | No inline AI refinement — users must manually edit or fully regenerate | Add a toolbar with targeted re-prompt actions ("Make Hook Punchier," "Shorten," "More Technical"). Each sends current draft + instruction to LLM. | Medium-High |
| **P1 (High)** | Reliability | No LLM call timeouts — hung providers block pipeline indefinitely | Add `request_timeout` and `max_retries` to all `_get_chat_model` provider configurations. Add a pre-flight health check before each run. | Medium-High |
| **P1 (High)** | Content | No factuality verification — LLM can hallucinate claims | Add a post-generation check: ask the LLM to list factual claims in the draft and verify each against the source summary. Flag unverifiable claims. | Medium |
| **P1 (High)** | Content | Analyst scoring is too vague — poor article selection for LinkedIn | Upgrade analyst prompt with multi-axis scoring (Contrarian Value, Technical Depth, Debate Potential, Timeliness, Source Credibility). Add pre-LLM heuristics to filter out press releases, funding news, and thin-summary articles. | High |
| **P1 (High)** | Automation | No scheduled runs or email notifications | Add `APScheduler` for cron-driven scout+analyst runs. Build email digest with curated candidates table, deep-links to UI, and "Skip" action. Add `scheduled_runs` SQLite table and UI panel. | High |
| **P2 (Medium)** | Content | No persona/post-format switching | Add a "Post Style" dropdown (Hot Take, Deep Dive, Bullet Summary, Thread) and a "Persona" dropdown (CTO, PM, VC, Engineer). Map each to prompt variants. | Medium |
| **P2 (Medium)** | UX | No draft version history or comparison | Store draft iterations per article in SQLite. Add a "Previous Versions" dropdown and side-by-side diff view. | Medium |
| **P2 (Medium)** | Performance | No response caching — re-runs re-fetch everything | Add a TTL-based cache for RSS feed responses and Google News results (e.g., 15-minute cache per feed URL). | Medium |
| **P2 (Medium)** | Content | Hashtag generation is unguided | Add a configurable hashtag library (per topic/domain) and inject relevant hashtags into the prompt as suggestions. | Low-Medium |
| **P3 (Low)** | UX | `alert()` calls are jarring | Replace all `alert()` calls with a lightweight toast notification system (CSS-animated, auto-dismiss). | Low |
| **P3 (Low)** | Code Quality | Domain config is localStorage-only | Add server-side domain persistence (SQLite table or JSON file) with API endpoints for save/load. Keep localStorage as cache layer. | Low |
| **P3 (Low)** | Content | No few-shot examples in prompts | Add 1-2 example input/output pairs to both analyst and author prompts to improve output consistency. | Low |
| **P3 (Low)** | Performance | URL enrichment fetches full HTML synchronously | Add a TTL cache for enrichment results. Consider using `trafilatura` for faster, more targeted metadata extraction. | Low |
| **P3 (Low)** | Feature | No export formats beyond clipboard | Add "Download as Markdown," "Download as Plain Text," and "Copy as HTML" options. | Low |

---

## Next Steps

1. **Immediate (this week):**
   - Replace `MemorySaver` with `SqliteSaver` to prevent state loss on restart. This is a drop-in change with no API impact.
   - Add `request_timeout` (60s) and `max_retries` (2) to all LLM provider configurations in `_get_chat_model()`.
   - Parallelize scout fetches with `ThreadPoolExecutor(max_workers=6)` to reduce scout latency from 60s to ~10s.

2. **Short-term (next 2 weeks):**
   - **Upgrade the analyst prompt with multi-axis scoring** (Contrarian Value, Technical Depth, Debate Potential, Timeliness, Source Credibility) and pre-LLM heuristics to filter out press releases, funding news, and thin-summary articles. This directly addresses the article selection quality problem.
   - **Implement scheduled runs with email digest.** Add `APScheduler` for cron-driven scout+analyst runs, `emailer.py` for SMTP-based HTML email delivery, a `scheduled_runs` SQLite table, and a "Scheduled Runs" panel in the UI. This eliminates the need to manually trigger runs and review results in-browser.
   - Implement SSE streaming for the author node. The LangGraph `astream_events()` API supports this natively.
   - Replace the article ID text input with a radio-button selection UI on candidate cards.

3. **Medium-term (next month):**
   - Add the inline AI refinement toolbar with 4-5 targeted re-prompt actions.
   - Implement persona and post-format switching with prompt variants.
   - Add draft version history with side-by-side comparison.

4. **Long-term (next quarter):**
   - LinkedIn API integration for direct posting and post-performance analytics.
   - Multi-format output (threads, carousel outlines, video scripts).
   - Persistent user style profile with past-performance feedback loop.
