import os
import unittest
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import graph


class DraftReviewActionTests(unittest.TestCase):
    def test_edit_without_draft_does_not_publish(self):
        state = {
            "final_draft": "existing draft",
            "selected_article_id": "7",
            "published_drafts": [],
        }

        with self.assertRaisesRegex(ValueError, "edited_draft is required"):
            graph._apply_draft_review_action(state, "edit", "")

    def test_unknown_action_does_not_publish(self):
        state = {
            "final_draft": "existing draft",
            "selected_article_id": "7",
            "published_drafts": [],
        }

        with self.assertRaisesRegex(ValueError, "Unsupported draft review action"):
            graph._apply_draft_review_action(state, "pubish", None)

    def test_publish_requires_explicit_publish_action(self):
        state = {
            "final_draft": "existing draft",
            "selected_article_id": "7",
            "published_drafts": [],
        }

        result = graph._apply_draft_review_action(state, "publish", None)

        self.assertEqual(result["workflow_status"], "published")
        self.assertEqual(result["published_drafts"][0]["draft"], "existing draft")
        self.assertEqual(result["published_drafts"][0]["article_id"], "7")

    def test_edit_approval_node_rejects_dict_without_action(self):
        state = {
            "final_draft": "existing draft",
            "selected_article_id": "7",
            "published_drafts": [],
        }

        with patch.object(graph, "interrupt", return_value={"selected_article_id": "8"}):
            with self.assertRaisesRegex(ValueError, "must include action"):
                graph.edit_approval_node(state)


class UrlSafetyTests(unittest.TestCase):
    def test_url_safety_rejects_local_and_private_targets(self):
        self.assertFalse(graph._is_public_http_url("file:///etc/passwd"))
        self.assertFalse(graph._is_public_http_url("http://localhost:8000"))
        self.assertFalse(graph._is_public_http_url("http://127.0.0.1:8000"))
        self.assertFalse(graph._is_public_http_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(graph._is_public_http_url("http://10.0.0.1/internal"))

    def test_url_safety_allows_public_http_targets(self):
        with patch.object(
            graph.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 443))],
        ):
            self.assertTrue(graph._is_public_http_url("https://example.com/article"))


class GoogleNewsFallbackTests(unittest.TestCase):
    def test_unknown_domain_routes_to_google_news(self):
        route = graph._resolve_domain_route("example.com")

        self.assertEqual(route["mode"], "google_news")

    def test_google_news_url_uses_site_and_recency_query(self):
        url = graph._build_google_news_rss_url(
            "example.com",
            "Agentic AI and Venture Capital from the last 72 hours",
            3,
        )

        self.assertIn("news.google.com/rss/search", url)
        self.assertIn("site%3Aexample.com", url)
        self.assertIn("when%3A3d", url)


class TopicAndPromptTests(unittest.TestCase):
    def test_topic_matching_keeps_acronyms(self):
        keywords = graph._topic_keywords("AI and ML infrastructure")

        self.assertIn("ai", keywords)
        self.assertIn("ml", keywords)
        self.assertTrue(graph._topic_matches(keywords, "AI chips", "New systems for training"))

    def test_analyst_prompt_uses_configured_max_age(self):
        captured = {}

        def capture_prompt(_llm, prompt):
            captured["prompt"] = prompt
            return graph.AnalystResponse(picks=[])

        state = {
            "raw_articles": [
                {
                    "id": "1",
                    "title": "AI systems update",
                    "url": "https://example.com/ai",
                    "source": "example.com",
                    "published_at": "2026-06-01T00:00:00+00:00",
                    "summary": "Useful context that is long enough to comfortably survive the pre-LLM heuristic filter's thin-summary check, well past the 150 character minimum threshold required to pass.",
                    "relevance_score": 0.0,
                }
            ],
            "topic": "AI systems",
            "analyst_provider": "ollama",
        }

        with patch.object(graph, "get_max_article_age_days", return_value=30), patch.object(
            graph, "_get_chat_model", return_value=object()
        ), patch.object(graph, "_invoke_analyst_structured", side_effect=capture_prompt):
            graph.analyst_node(state)

        self.assertIn("published in the last 30 days", captured["prompt"])


class HeuristicPrefilterTests(unittest.TestCase):
    GOOD_SUMMARY = (
        "A survey of 200 production agent deployments reveals 5 common failure "
        "modes including state corruption and unbounded retry loops in agentic systems. "
        "The authors propose a checkpoint-and-replay pattern that reduced incidents by 60%."
    )

    def test_keeps_articles_with_substantial_summaries_and_neutral_titles(self):
        articles = [{"id": "1", "title": "Why Agentic Workflows Break in Production", "summary": self.GOOD_SUMMARY}]

        kept, stats = graph._heuristic_prefilter(articles)

        self.assertEqual(len(kept), 1)
        self.assertEqual(stats["kept_count"], 1)
        self.assertEqual(stats["dropped_thin_summary"], 0)
        self.assertEqual(stats["dropped_press_release_pattern"], 0)

    def test_drops_thin_summary(self):
        articles = [{"id": "1", "title": "Some Article", "summary": "Too short."}]

        kept, stats = graph._heuristic_prefilter(articles)

        self.assertEqual(len(kept), 0)
        self.assertEqual(stats["dropped_thin_summary"], 1)

    def test_drops_funding_announcement_by_title(self):
        articles = [{"id": "1", "title": "Acme Corp Raises $50M Series B", "summary": self.GOOD_SUMMARY}]

        kept, stats = graph._heuristic_prefilter(articles)

        self.assertEqual(len(kept), 0)
        self.assertEqual(stats["dropped_press_release_pattern"], 1)

    def test_drops_executive_appointment_press_release(self):
        articles = [{"id": "1", "title": "Acme Corp Appoints Jane Doe as New CEO", "summary": self.GOOD_SUMMARY}]

        kept, stats = graph._heuristic_prefilter(articles)

        self.assertEqual(len(kept), 0)
        self.assertEqual(stats["dropped_press_release_pattern"], 1)

    def test_does_not_false_positive_on_technical_titles_mentioning_series(self):
        articles = [{"id": "1", "title": "A New Series of Benchmarks for Agentic AI Systems", "summary": self.GOOD_SUMMARY}]

        kept, stats = graph._heuristic_prefilter(articles)

        self.assertEqual(len(kept), 1)


class AuthorPersonaTests(unittest.TestCase):
    ARTICLE = {
        "id": "1",
        "title": "Test Article",
        "url": "https://example.com/a",
        "source": "example.com",
        "published_at": "2026-06-01T00:00:00+00:00",
        "summary": "Useful context",
        "relevance_score": 8.0,
    }

    def test_each_known_persona_produces_distinguishing_voice_text(self):
        for persona_id, config in graph.PERSONAS.items():
            prompt = graph._build_author_prompt(self.ARTICLE, None, persona_id)
            self.assertIn(config["voice"], prompt)
            self.assertIn(config["structure"], prompt)

    def test_unrecognized_persona_falls_back_to_default(self):
        prompt = graph._build_author_prompt(self.ARTICLE, None, "nonexistent_persona")
        default_config = graph.PERSONAS[graph.DEFAULT_PERSONA]
        self.assertIn(default_config["voice"], prompt)


if __name__ == "__main__":
    unittest.main()
