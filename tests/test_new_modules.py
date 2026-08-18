import os
import shutil
import tempfile
import unittest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import feedparser

import domain_store
import feed_cache
import graph


SAMPLE_FEED_XML = (
    '<?xml version="1.0"?><rss version="2.0"><channel><title>Test Feed</title>'
    "<item><title>Hello World</title><link>http://example.com/a</link>"
    "<description>desc</description></item></channel></rss>"
)


class FeedCacheRoundTripTests(unittest.TestCase):
    """Regression test for the cache corrupting every hit (Bug 1): the cached
    value must be the raw feed text, not str(parsed_feed), so it re-parses
    into the same entries on the next call."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.original_db_path = feed_cache.DB_PATH
        feed_cache.DB_PATH = os.path.join(self.tmp_dir, "test_cache.db")
        feed_cache.init_db()

    def tearDown(self):
        feed_cache.DB_PATH = self.original_db_path
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_cached_value_reparses_into_same_entries(self):
        feed_cache.set("http://example.com/feed", SAMPLE_FEED_XML)

        cached = feed_cache.get("http://example.com/feed")
        self.assertIsNotNone(cached)

        reparsed = feedparser.parse(cached)
        self.assertFalse(getattr(reparsed, "bozo", False))
        self.assertEqual(len(reparsed.entries), 1)
        self.assertEqual(reparsed.entries[0].title, "Hello World")

    def test_get_returns_none_for_uncached_url(self):
        self.assertIsNone(feed_cache.get("http://example.com/never-cached"))

    def test_expired_entry_is_not_returned(self):
        feed_cache.set("http://example.com/feed", SAMPLE_FEED_XML, ttl_seconds=-1)
        self.assertIsNone(feed_cache.get("http://example.com/feed"))


class DomainStoreTests(unittest.TestCase):
    """Regression test for scheduled runs ignoring UI-curated domains (Bug 3):
    the store must round-trip an enabled/disabled domain list so the
    scheduler can read the same set the UI persists."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.original_db_path = domain_store.DB_PATH
        domain_store.DB_PATH = os.path.join(self.tmp_dir, "test_domains.db")
        domain_store.init_db()

    def tearDown(self):
        domain_store.DB_PATH = self.original_db_path
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_disabled_domains_excluded_from_enabled_list(self):
        domain_store.save_domains(["a.com", "b.com", "c.com"], disabled=["b.com"])

        enabled = domain_store.get_enabled_domains()

        self.assertIn("a.com", enabled)
        self.assertIn("c.com", enabled)
        self.assertNotIn("b.com", enabled)

    def test_saving_again_updates_enabled_state(self):
        domain_store.save_domains(["a.com"], disabled=[])
        self.assertIn("a.com", domain_store.get_enabled_domains())

        domain_store.save_domains(["a.com"], disabled=["a.com"])
        self.assertNotIn("a.com", domain_store.get_enabled_domains())

    def test_get_domains_reports_enabled_flag(self):
        domain_store.save_domains(["a.com", "b.com"], disabled=["b.com"])

        rows = {row["domain"]: row["enabled"] for row in domain_store.get_domains()}

        self.assertTrue(rows["a.com"])
        self.assertFalse(rows["b.com"])


class BuildGraphCheckpointerTests(unittest.TestCase):
    """Regression test for the checkpointer connection being garbage-collected
    (and closed) the instant build_graph() returns, because nothing kept the
    SqliteSaver.from_conn_string() contextmanager generator alive. This broke
    every single graph invocation with 'Cannot operate on a closed database.'"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.original_env = os.environ.get("CHECKPOINT_DB_PATH")
        os.environ["CHECKPOINT_DB_PATH"] = os.path.join(self.tmp_dir, "test_checkpoints.db")

    def tearDown(self):
        if self.original_env is None:
            os.environ.pop("CHECKPOINT_DB_PATH", None)
        else:
            os.environ["CHECKPOINT_DB_PATH"] = self.original_env
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_checkpointer_connection_survives_build_graph_return(self):
        app = graph.build_graph()
        # Would previously raise sqlite3.ProgrammingError: Cannot operate on a
        # closed database, because build_graph() closed its own connection
        # before returning.
        state = app.get_state({"configurable": {"thread_id": "smoke-test"}})
        self.assertEqual(state.values, {})


if __name__ == "__main__":
    unittest.main()
