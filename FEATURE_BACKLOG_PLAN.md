# Feature Backlog — Phased Plan (2026-08-23)

Sequencing for the 13 open feature-backlog items from `AUDIT_REPORT_2026-08-18.md` §8. All bugs are fixed (see that report); this plan covers net-new work only. Nothing here is started — this is a proposal for review before any implementation begins.

Grouped by dependency and effort, not just P2/P3 label — a few P3 items are cheap and valuable enough to pull forward, and a couple of "never started" items are large enough to split further once we get there.

---

## Phase 1 — Foundational / low-risk wins
Self-contained, no dependencies on other backlog items, immediate reliability or UX payoff.

| Item | Effort | Why first |
|:---|:---|:---|
| Docker volume mounts for `checkpoints.db`/`cache.db`/`domains.db`/`scheduled_runs.db` | S | Pure config; currently any containerized deployment silently loses all scheduler/domain/checkpoint state on restart. Cheapest possible risk reduction. |
| Toast notifications (replace `alert()`) | S | Self-contained JS/CSS change, ~9 call sites. No backend changes. Improves every other UI feature we touch next, so doing it early avoids re-touching those call sites twice. |
| Cost / token spend visibility | M | No dependencies; add token-count logging + a simple per-run cost estimate (log line first, UI display can follow). Audit flagged this as the single biggest gap for non-Ollama users, especially with the scheduler running unattended. |

## Phase 2 — Draft workflow improvements
Builds on existing `draft_store.py`/refine toolbar; naturally sequenced together since they all touch the same draft-editing surface.

| Item | Effort | Why here |
|:---|:---|:---|
| Draft version history / undo | M | Highest-cited pain point — refine toolbar currently overwrites in place with no way back. Needs a versions table/list in `draft_store.py` + minimal UI (undo button, maybe a version dropdown). |
| Export formats beyond clipboard | S–M | Piggybacks on the draft-display code touched by version history. |
| Persona / post-format switching | M | Touches author prompt + adds a UI dropdown; natural to build alongside export formats since both modify the "how the draft is produced/shown" surface. |
| Hashtag library | S | Small, additive; convenient to bundle with persona work since both extend the author-side UI panel. |

## Phase 3 — Cost/analyst efficiency
One item, but a distinct concern (server-side LLM cost control) worth its own pass with focused testing.

| Item | Effort | Why here |
|:---|:---|:---|
| Pre-LLM heuristic filtering for the analyst | M | Needs care — the AUTO-REJECT criteria are currently prompt-only; a bad heuristic could silently drop good candidates. Wants dedicated test coverage (this was flagged as a zero-test area) rather than being bundled with UI work. |

## Phase 4 — External integrations
Larger scope, external API/auth dependencies, higher blast radius (real publishing, external triggers) — sequenced after the app itself is in better shape.

| Item | Effort | Why here |
|:---|:---|:---|
| Webhook trigger for `/api/start` | M | Needs auth/security design (who can trigger a run remotely) before it's safe to expose. |
| LinkedIn API direct-publish | L | Requires LinkedIn app registration, OAuth flow, API review — the single largest individual item. Also the highest-value one (removes the last manual step in the pipeline), so it anchors this phase. |

## Phase 5 — Advanced / needs design first
Biggest scope, least defined — each of these needs a short design pass (data model, UI shape) before implementation starts, so they're last.

| Item | Effort | Why here |
|:---|:---|:---|
| Persistent user style profile / feedback loop | L | Needs a data collection strategy (what signal counts as "good"?) before any code. |
| Multi-format output (threads, carousels, video scripts) | L | Per-format prompt engineering + UI, multiplies scope of Phase 2's persona work — better attempted once persona switching exists as a foundation. |
| Topic/source performance dashboard | L | Needs metrics collection over time before there's anything to display — the least urgent since it has no payoff until other phases have been running long enough to generate data. |

---

## Notes
- Effort key: S = hours, M = ~1 day, L = multi-day / needs its own design discussion.
- Phases are proposed sequencing, not hard gates — happy to reorder based on what you actually want next (e.g. pull LinkedIn direct-publish forward if that's the priority despite its size).
- No PR has been opened for `fix/scheduler-cache-review` yet; worth doing before starting Phase 1 so backlog work lands on top of a merged, reviewed baseline rather than stacking further on an unreviewed branch.
