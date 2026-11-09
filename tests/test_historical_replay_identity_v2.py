"""Static regression guard: historical replay never falls back to a display name."""
import ast
from pathlib import Path
import unittest


class HistoricalReplayIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
        cls.function = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "replay_saved_trucksbook_jobs"
        )
        cls.source = ast.get_source_segment(
            Path("bot.py").read_text(encoding="utf-8"), cls.function
        )

    def test_replay_uses_exact_provider_id(self):
        self.assertIn("WHERE trucksbook_user_id=$1", self.source)
        self.assertIn("numeric_account = int(account)", self.source)
        self.assertNotIn("find_driver_link(", self.source)

    def test_replay_keeps_unlinked_accounts_pending(self):
        self.assertIn("if driver is None:", self.source)
        self.assertIn("held += 1", self.source)

    def test_replay_does_not_recredit_processed_jobs(self):
        self.assertIn("NOT EXISTS(SELECT 1 FROM processed_jobs", self.source)


if __name__ == "__main__":
    unittest.main()
