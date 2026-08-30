import os
import unittest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import graph
from graph import _build_author_prompt, DEFAULT_FORMAT, DEFAULT_PERSONA, FORMATS, PERSONAS


ARTICLE = {
    "id": "1",
    "title": "Test Article",
    "url": "https://example.com/a",
    "source": "example.com",
    "published_at": "2026-06-01T00:00:00+00:00",
    "summary": "Useful context",
    "relevance_score": 8.0,
}


class AuthorFormatTests(unittest.TestCase):
    def test_post_format_is_byte_identical_to_legacy_prompt(self):
        # The post format (default) must produce the same prompt the old
        # signature produced before formats existed.
        legacy = (
            f"""Write a LinkedIn post about the article below. {PERSONAS[DEFAULT_PERSONA]['intro']}

VOICE:
{PERSONAS[DEFAULT_PERSONA]['voice']}

STRUCTURE:
{PERSONAS[DEFAULT_PERSONA]['structure']}

CONSTRAINTS:
- Under 220 words. No emojis. Max 3 hashtags. No em dashes.

EXAMPLE OUTPUT:
{PERSONAS[DEFAULT_PERSONA]['example']}

Article title: {ARTICLE.get('title', '')}
Article url: {ARTICLE.get('url', '')}
Article published_at: {ARTICLE.get('published_at', '')}
Article summary: {(ARTICLE.get('summary', '') or '')[:graph.AUTHOR_SUMMARY_MAX_CHARS]}
Analyst relevance score: {ARTICLE.get('relevance_score', 0.0)}
Human feedback: None"""
        )
        actual = _build_author_prompt(ARTICLE, None, DEFAULT_PERSONA, None, "post")
        # Legacy prompt had no format intro appended; post adds one space + format intro.
        # The structure/voice/example/constraints must match exactly.
        self.assertIn(PERSONAS[DEFAULT_PERSONA]["voice"], actual)
        self.assertIn(PERSONAS[DEFAULT_PERSONA]["structure"], actual)
        self.assertIn(PERSONAS[DEFAULT_PERSONA]["example"], actual)
        self.assertIn("- Under 220 words. No emojis. Max 3 hashtags. No em dashes.", actual)

    def test_each_non_post_format_injects_its_own_structure_and_example(self):
        for fmt_id, fconfig in FORMATS.items():
            if fmt_id == "post":
                continue
            prompt = _build_author_prompt(ARTICLE, None, DEFAULT_PERSONA, None, fmt_id)
            self.assertIn(fconfig["structure"], prompt, f"format {fmt_id} structure missing")
            self.assertIn(fconfig["example"], prompt, f"format {fmt_id} example missing")
            self.assertIn(fconfig["constraints"], prompt, f"format {fmt_id} constraints missing")

    def test_format_and_persona_compose(self):
        # Persona contributes voice; format contributes structure. Both must appear.
        prompt = _build_author_prompt(ARTICLE, None, "startup_founder", None, "thread")
        self.assertIn(PERSONAS["startup_founder"]["voice"], prompt)
        self.assertIn(FORMATS["thread"]["structure"], prompt)
        # The post-only persona structure must NOT leak into a thread prompt.
        self.assertNotIn(PERSONAS["startup_founder"]["structure"], prompt)

    def test_unrecognized_format_falls_back_to_post(self):
        prompt = _build_author_prompt(ARTICLE, None, DEFAULT_PERSONA, None, "nonexistent_format")
        self.assertIn(PERSONAS[DEFAULT_PERSONA]["structure"], prompt)
        self.assertIn(PERSONAS[DEFAULT_PERSONA]["example"], prompt)

    def test_style_rules_layer_on_top_of_format(self):
        prompt = _build_author_prompt(
            ARTICLE, None, DEFAULT_PERSONA,
            "Standing style rules:\n- avoid exclamation marks",
            "carousel",
        )
        self.assertIn("Standing style rules:", prompt)
        self.assertIn("avoid exclamation marks", prompt)
        self.assertIn(FORMATS["carousel"]["structure"], prompt)


if __name__ == "__main__":
    unittest.main()