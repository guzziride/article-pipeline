# Future Features

Improvements we can implement one at a time:

1. Persist run history in SQLite so approvals/drafts survive server restarts.
2. Add user authentication for the UI (single-user password or OAuth).
3. Add per-step latency/cost tracking for Tavily, analyst, and writer nodes.
4. Add article deduplication across runs using URL canonicalization + hashing.
5. Add configurable analyst scoring criteria weights (MCP, agentic, SaaS, novelty).
6. Add richer HITL actions: approve, reject, request re-rank, or ask for more candidates.
7. Add draft versioning so each resume/edit generates a new saved version.
8. Add in-browser draft editor with “rewrite with feedback” actions.
9. Add scheduling/cron to run scouting daily and notify when new candidates appear.
10. Add export options for drafts (Markdown, Notion, Google Docs, LinkedIn-ready copy).
11. Add source-quality filters (domain allow/deny list, recency window, language).
12. Add observability dashboard for workflow_status, interrupts, failures, and retries.
13. Add retry/error policies per node with clear UI recovery controls.
14. Add multi-thread workspace view with search, tags, and archive.
15. Add automated tests (unit tests for nodes + integration tests for HITL resume).
16. Add containerized deployment (Docker + docker-compose) for reproducible setup.
17. Add optional Redis/Postgres checkpointer for production-grade state persistence.
18. Add role-based prompts/profiles (CTO, Product, Researcher) switchable in UI.
19. Add citation-aware drafting so each key claim maps back to source URLs.
20. Add approval analytics (which topics/sources convert to accepted drafts best).
21. “Running…” spinner while request is in progress  
22. Node-by-node progress banner (Scout -> Analyst -> Waiting Approval -> Author -> Done)  
23. Last successful run timestamp + duration at top of UI
24. Add direct RSS feed ingestion (for trusted sources) as an optional scout input to improve publish-date metadata quality and reduce false `missing_publish_date` drops.
