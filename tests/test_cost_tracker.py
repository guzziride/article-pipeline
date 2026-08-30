import os
import shutil
import tempfile
import unittest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import cost_tracker


class CostTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.original_db_path = cost_tracker.DB_PATH
        cost_tracker.DB_PATH = os.path.join(self.tmp_dir, "test_costs.db")
        cost_tracker.init_db()

    def tearDown(self):
        cost_tracker.DB_PATH = self.original_db_path
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_log_usage_computes_cost_for_known_priced_model(self):
        # gpt-4o: (2.50, 10.00) $/1M tokens
        cost = cost_tracker.log_usage(
            "thread-1", "author_draft", "openai", "gpt-4o", 1_000_000, 1_000_000, estimated=False
        )
        self.assertAlmostEqual(cost, 2.50 + 10.00)

    def test_get_thread_cost_aggregates_across_nodes(self):
        cost_tracker.log_usage("thread-1", "analyst", "openai", "gpt-4o", 1_000_000, 0, estimated=True)
        cost_tracker.log_usage("thread-1", "author_draft", "openai", "gpt-4o", 0, 1_000_000, estimated=False)
        cost_tracker.log_usage("thread-2", "analyst", "openai", "gpt-4o", 1_000_000, 0, estimated=True)

        result = cost_tracker.get_thread_cost("thread-1")

        self.assertAlmostEqual(result["total_cost_usd"], 2.50 + 10.00)
        self.assertIn("analyst", result["by_node"])
        self.assertIn("author_draft", result["by_node"])
        self.assertTrue(result["estimated"])  # analyst row was estimated

    def test_unrecognized_model_falls_back_to_default_rate(self):
        cost = cost_tracker.log_usage(
            "thread-1", "analyst", "some-new-provider", "some-new-model", 1_000_000, 1_000_000, estimated=True
        )
        self.assertAlmostEqual(cost, 1.00 + 3.00)

    def test_ollama_is_always_free(self):
        cost = cost_tracker.log_usage(
            "thread-1", "author_draft", "ollama", "llama3.1", 1_000_000, 1_000_000, estimated=False
        )
        self.assertEqual(cost, 0.0)

    def test_get_recent_totals_counts_calls_and_cost(self):
        cost_tracker.log_usage("thread-1", "analyst", "openai", "gpt-4o", 1_000_000, 0, estimated=True)
        cost_tracker.log_usage("thread-1", "author_draft", "openai", "gpt-4o", 0, 1_000_000, estimated=False)

        totals = cost_tracker.get_recent_totals(hours=24)

        self.assertEqual(totals["call_count"], 2)
        self.assertAlmostEqual(totals["total_cost_usd"], 2.50 + 10.00)


if __name__ == "__main__":
    unittest.main()
