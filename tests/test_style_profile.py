import os
import shutil
import tempfile
import unittest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import style_profile


class StyleProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.original_db_path = style_profile.DB_PATH
        style_profile.DB_PATH = os.path.join(self.tmp_dir, "test_style.db")
        style_profile.init_db()

    def tearDown(self):
        style_profile.DB_PATH = self.original_db_path
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_and_list_rule(self):
        r = style_profile.add_rule("keep posts under 220 words")
        self.assertEqual(r["rule_text"], "keep posts under 220 words")
        self.assertEqual(r["persona"], "*")
        self.assertEqual(r["source"], "manual")
        self.assertFalse(r["disabled"])
        rules = style_profile.list_rules()
        self.assertEqual(len(rules), 1)

    def test_add_rejects_empty_rule(self):
        with self.assertRaises(ValueError):
            style_profile.add_rule("   ")

    def test_get_active_rules_filters_by_persona_and_disabled(self):
        style_profile.add_rule("always rule", persona="*")
        style_profile.add_rule("persona rule", persona="cto_phd")
        rid = style_profile.add_rule("disabled rule", persona="startup_founder")
        style_profile.set_disabled(rid["id"], True)

        # No persona passed -> only the '*' (always) rules match.
        active_default = style_profile.get_active_rules()
        self.assertEqual(len(active_default), 1)
        self.assertEqual(active_default[0]["rule_text"], "always rule")

        # cto_phd persona -> '*' rules + persona-specific.
        active_cto = style_profile.get_active_rules("cto_phd")
        self.assertEqual(len(active_cto), 2)
        texts = {r["rule_text"] for r in active_cto}
        self.assertEqual(texts, {"always rule", "persona rule"})

        # startup_founder persona -> only '*' (its own rule is disabled).
        active_founder = style_profile.get_active_rules("startup_founder")
        self.assertEqual(len(active_founder), 1)
        self.assertEqual(active_founder[0]["rule_text"], "always rule")

    def test_increment_applied_updates_count(self):
        r = style_profile.add_rule("avoid exclamation marks")
        style_profile.increment_applied([r["id"]])
        style_profile.increment_applied([r["id"]])
        fetched = style_profile.get_rule(r["id"])
        self.assertEqual(fetched["applied_count"], 2)

    def test_active_rules_block_returns_none_when_empty(self):
        self.assertIsNone(style_profile.active_rules_block())

    def test_active_rules_block_formats_lines(self):
        style_profile.add_rule("open with a contrarian one-liner")
        block = style_profile.active_rules_block()
        self.assertIn("Standing style rules:", block)
        self.assertIn("open with a contrarian one-liner", block)

    def test_delete_rule(self):
        r = style_profile.add_rule("temporary")
        self.assertTrue(style_profile.delete_rule(r["id"]))
        self.assertIsNone(style_profile.get_rule(r["id"]))


if __name__ == "__main__":
    unittest.main()