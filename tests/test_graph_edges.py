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


class PaywallDomainAndMarkerTests(unittest.TestCase):
    def _entry(self, title, link, summary, published="2026-08-29T00:00:00Z"):
        class _E:
            pass
        e = _E()
        e.title = title
        e.link = link
        e.summary = summary
        e.published = published
        e.published_parsed = None
        e.updated_parsed = None
        e.created_parsed = None
        e.updated = None
        e.created = None
        e.dc_date = None
        return e

    def test_drops_paywall_domain(self):
        feed = type("F", (), {"entries": [self._entry("Exclusive", "https://www.theinformation.com/a", "A long enough summary about the article content here.")]})
        with patch.object(graph, "get_paywalled_domains", return_value={"theinformation.com"}), patch.object(graph, "get_paywall_markers", return_value=[]):
            normalized, audit, stats = graph._normalize_rss_entries("theinformation.com", "http://feed", feed, 0)
        self.assertEqual(len(normalized), 0)
        self.assertEqual(stats["dropped_paywall_domain"], 1)
        self.assertEqual(audit[0]["drop_reason"], "paywall_domain")

    def test_drops_paywall_marker_in_summary(self):
        feed = type("F", (), {"entries": [self._entry("Some post", "https://substack.example.com/x", "This post is for paid subscribers only. Here is the teaser.")]})
        with patch.object(graph, "get_paywalled_domains", return_value=set()), patch.object(graph, "get_paywall_markers", return_value=["this post is for paid subscribers"]):
            normalized, audit, stats = graph._normalize_rss_entries("substack.example.com", "http://feed", feed, 0)
        self.assertEqual(len(normalized), 0)
        self.assertEqual(stats["dropped_paywall_marker"], 1)
        self.assertEqual(audit[0]["drop_reason"], "paywall_marker")

    def test_keeps_free_substack_post(self):
        feed = type("F", (), {"entries": [self._entry("How MCP works", "https://latent.space/x", "A detailed free post about Model Context Protocol internals and patterns.")]})
        with patch.object(graph, "get_paywalled_domains", return_value=set()), patch.object(graph, "get_paywall_markers", return_value=["this post is for paid subscribers"]):
            normalized, audit, stats = graph._normalize_rss_entries("latent.space", "http://feed", feed, 0)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(stats["dropped_paywall_marker"], 0)


class PaywallProbeTests(unittest.TestCase):
    def _fake_response(self, html: str, status: int = 200):
        m = unittest.mock.MagicMock()
        m.headers.get_content_charset.return_value = "utf-8"
        m.read.return_value = html.encode("utf-8")
        m.__enter__ = lambda s: m
        m.__exit__ = lambda s, *a: False
        m.status = status
        return m

    def test_is_paywalled_marker_in_body(self):
        with patch.object(graph, "_is_public_http_url", return_value=True), patch.object(
            graph.urllib.request, "urlopen", return_value=self._fake_response("This post is for paid subscribers only. <p>full article</p>")
        ):
            is_pw, reason = graph._is_paywalled_article("https://substack.example.com/x")
        self.assertTrue(is_pw)
        self.assertTrue(reason.startswith("marker:"))

    def test_is_paywalled_http_403(self):
        with patch.object(graph, "_is_public_http_url", return_value=True), patch.object(
            graph.urllib.request, "urlopen", side_effect=graph.urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        ):
            is_pw, reason = graph._is_paywalled_article("https://wsj.example.com/x")
        self.assertTrue(is_pw)
        self.assertEqual(reason, "http_403")

    def test_not_paywalled_free_article(self):
        with patch.object(graph, "_is_public_http_url", return_value=True), patch.object(
            graph.urllib.request, "urlopen", return_value=self._fake_response("<html><title>How Agentic AI Works</title><p>full article body</p></html>")
        ):
            is_pw, reason = graph._is_paywalled_article("https://openai.com/research/x")
        self.assertFalse(is_pw)
        self.assertEqual(reason, "")

    def test_fetch_error_fails_open(self):
        with patch.object(graph, "_is_public_http_url", return_value=True), patch.object(
            graph.urllib.request, "urlopen", side_effect=Exception("network down")
        ):
            is_pw, reason = graph._is_paywalled_article("https://openai.com/research/x")
        self.assertFalse(is_pw)
        self.assertEqual(reason, "")

    def test_non_public_url_skips_probe(self):
        with patch.object(graph, "_is_public_http_url", return_value=False):
            is_pw, reason = graph._is_paywalled_article("http://localhost/x")
        self.assertFalse(is_pw)


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

    def test_prompt_includes_anti_ai_tells(self):
        prompt = graph._build_author_prompt(self.ARTICLE, None, graph.DEFAULT_PERSONA)
        self.assertIn("delve", prompt)
        self.assertIn("Do NOT use em dashes", prompt)

    def test_prompt_uses_article_body_when_provided(self):
        body = "This is the full article body with much more detail than the summary."
        prompt = graph._build_author_prompt(self.ARTICLE, None, graph.DEFAULT_PERSONA, article_body=body)
        self.assertIn("Article body:", prompt)
        self.assertIn(body, prompt)

    def test_prompt_falls_back_to_summary_when_no_body(self):
        prompt = graph._build_author_prompt(self.ARTICLE, None, graph.DEFAULT_PERSONA)
        self.assertIn("Article summary:", prompt)
        self.assertIn("Useful context", prompt)

    def test_prompt_includes_writer_examples(self):
        with patch.object(graph, "get_writer_examples", return_value=["My actual writing style. Blunt and direct."]):
            prompt = graph._build_author_prompt(self.ARTICLE, None, graph.DEFAULT_PERSONA)
        self.assertIn("how I actually write", prompt)
        self.assertIn("My actual writing style", prompt)

    def test_prompt_omits_examples_section_when_empty(self):
        with patch.object(graph, "get_writer_examples", return_value=[]):
            prompt = graph._build_author_prompt(self.ARTICLE, None, graph.DEFAULT_PERSONA)
        self.assertNotIn("how I actually write", prompt)


if __name__ == "__main__":
    unittest.main()
