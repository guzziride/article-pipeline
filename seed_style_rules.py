"""Seed the default standing style rules into style_profile.db.

Idempotent: rules that already exist (matched by rule_text) are skipped, so it
is safe to run repeatedly. Rules are stored in style_profile.db, which is
gitignored, so this script is the source of truth for a fresh clone.

Usage:
    python seed_style_rules.py
    docker compose exec -T article-pipeline python seed_style_rules.py
"""

import style_profile

DEFAULT_RULES = [
    "Keep the first sentence under 12 words; lead with a concrete fact, quote, or number, not a setup.",
    "No em-dashes at all. Use periods or commas instead.",
    "No buzzwords: never use delve, unlock, tapestry, robust, leverage, or game-changer.",
    "Include one first-person anecdote in parentheses for human voice.",
    "End with one specific debate question, not a summary sentence.",
    "Make it sound human, not like AI: add personalization, opinions, and rough edges.",
]


def main() -> None:
    existing = {r["rule_text"] for r in style_profile.list_rules(include_disabled=True)}
    added = 0
    for rule in DEFAULT_RULES:
        if rule in existing:
            continue
        style_profile.add_rule(rule, persona="*", source="manual")
        added += 1
    print(f"Seeded {added} new rule(s); {len(DEFAULT_RULES) - added} already present.")


if __name__ == "__main__":
    main()
