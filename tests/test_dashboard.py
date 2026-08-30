import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import cost_tracker
import draft_store
import scheduled_store
import style_profile
import dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self._orig = {
            "cost": cost_tracker.DB_PATH,
            "draft": draft_store.DB_PATH,
            "sched": scheduled_store.DB_PATH,
            "style": style_profile.DB_PATH,
        }
        cost_tracker.DB_PATH = os.path.join(self.tmp_dir, "costs.db")
        draft_store.DB_PATH = os.path.join(self.tmp_dir, "drafts.db")
        scheduled_store.DB_PATH = os.path.join(self.tmp_dir, "scheduled.db")
        style_profile.DB_PATH = os.path.join(self.tmp_dir, "style.db")
        cost_tracker.init_db()
        draft_store.init_db()
        scheduled_store.init_db()
        style_profile.init_db()

    def tearDown(self):
        cost_tracker.DB_PATH = self._orig["cost"]
        draft_store.DB_PATH = self._orig["draft"]
        scheduled_store.DB_PATH = self._orig["sched"]
        style_profile.DB_PATH = self._orig["style"]
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _seed_costs(self):
        now = datetime.now(timezone.utc)
        cost_tracker.log_usage("t1", "author_draft", "openai", "gpt-4o", 1000, 2000, False)
        cost_tracker.log_usage("t1", "analyst", "ollama", "llama3.1", 500, 100, True)
        cost_tracker.log_usage("t2", "refine", "gemini", "gemini-3.6-flash", 300, 800, False)

    def _seed_runs(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        scheduled_store.store_run("scheduled-1", "scheduled-1", "MCP", [])
        scheduled_store.store_run("scheduled-2", "scheduled-2", "MCP", [{"id": "1"}, {"id": "2"}])
        scheduled_store.mark_email_sent("scheduled-2")
        scheduled_store.mark_reviewed("scheduled-2")
        scheduled_store.store_run("webhook-1", "webhook-1", "agentic AI", [{"id": "1"}])

    def _seed_drafts(self):
        draft_store.add_draft("t1", "1", "A" * 200, datetime.now(timezone.utc).isoformat())
        draft_store.add_draft("t2", "3", "B" * 300, datetime.now(timezone.utc).isoformat())
        draft_store.add_version("t1", "draft1", "author")
        draft_store.add_version("t1", "draft1 refined", "refine: Shorten")
        draft_store.add_version("t2", "draft2", "author")

    def _seed_style_rules(self):
        style_profile.add_rule("keep short", persona="*")
        style_profile.add_rule("no emojis", persona="cto_phd")
        r = style_profile.add_rule("old rule", persona="*")
        style_profile.set_disabled(r["id"], True)
        style_profile.increment_applied([1])

    def test_cost_summary_aggregates_by_node_and_provider(self):
        self._seed_costs()
        result = dashboard.cost_summary(days=30)
        self.assertEqual(result["call_count"], 3)
        self.assertIn("author_draft", result["by_node"])
        self.assertIn("openai", result["by_provider"])
        self.assertGreater(result["total_cost_usd"], 0)

    def test_run_summary_computes_rates_and_topic_distribution(self):
        self._seed_runs()
        result = dashboard.run_summary(days=30)
        self.assertEqual(result["total_runs"], 3)
        self.assertEqual(result["total_emailed"], 1)
        self.assertEqual(result["total_reviewed"], 1)
        self.assertIn("MCP", result["by_topic"])
        self.assertEqual(result["by_topic"]["MCP"], 2)
        self.assertGreater(len(result["weekly"]), 0)

    def test_draft_summary_counts_published_and_versions(self):
        self._seed_drafts()
        result = dashboard.draft_summary(days=30)
        self.assertEqual(result["total_published"], 2)
        self.assertEqual(result["refine_count"], 1)
        self.assertEqual(result["author_count"], 2)
        self.assertGreater(result["avg_draft_length"], 0)

    def test_style_rule_usage_reports_active_and_disabled(self):
        self._seed_style_rules()
        result = dashboard.style_rule_usage()
        self.assertEqual(result["total_rules"], 3)
        self.assertEqual(result["active_rules"], 2)
        self.assertEqual(result["disabled_rules"], 1)
        self.assertEqual(result["rules"][0]["applied_count"], 1)

    def test_build_dashboard_returns_all_sections(self):
        self._seed_costs()
        self._seed_runs()
        self._seed_drafts()
        self._seed_style_rules()
        result = dashboard.build_dashboard(days=30)
        for key in ("cost", "runs", "drafts", "topics", "style_rules"):
            self.assertIn(key, result)

    def test_empty_dashboard_does_not_crash(self):
        result = dashboard.build_dashboard(days=30)
        self.assertEqual(result["cost"]["total_cost_usd"], 0)
        self.assertEqual(result["runs"]["total_runs"], 0)
        self.assertEqual(result["drafts"]["total_published"], 0)
        self.assertEqual(result["style_rules"]["total_rules"], 0)


if __name__ == "__main__":
    unittest.main()