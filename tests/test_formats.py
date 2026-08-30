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
    def test_post_format_includes_persona_voice_and_structure(self):
        prompt = _build_author_prompt(ARTICLE, None, DEFAULT_PERSONA, None, "post")
        self.assertIn(PERSONAS[DEFAULT_PERSONA]["voice"], prompt)
        self.assertIn(PERSONAS[DEFAULT_PERSONA]["structure"], prompt)
        self.assertIn("- Under 220 words. No emojis. Max 3 hashtags. No em dashes.", prompt)

    def test_each_non_post_format_injects_its_own_structure(self):
        for fmt_id, fconfig in FORMATS.items():
            if fmt_id == "post":
                continue
            prompt = _build_author_prompt(ARTICLE, None, DEFAULT_PERSONA, None, fmt_id)
            self.assertIn(fconfig["structure"], prompt, f"format {fmt_id} structure missing")
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