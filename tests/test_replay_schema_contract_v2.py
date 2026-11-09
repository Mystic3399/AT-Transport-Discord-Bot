"""Release-time guard against unsafe historical replay identity fallbacks.

This test inspects the exact replay function, ensuring the account link is
queried by immutable TrucksBook ID and never by mutable display name.
"""
import ast
from pathlib import Path
import unittest


class ReplaySchemaContractTests(unittest.TestCase):
    def test_saved_link_schema_and_replay_query_agree(self):
        source = Path("bot.py").read_text(encoding="utf-8")
        self.assertIn("trucksbook_user_id BIGINT UNIQUE", source)
        self.assertIn("WHERE trucksbook_user_id=$1", source)
        self.assertIn("SELECT discord_user_id FROM driver_links", source)

    def test_replay_never_mutates_driver_links(self):
        source = Path("bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "replay_saved_trucksbook_jobs")
        body = ast.get_source_segment(source, fn)
        self.assertNotIn("UPDATE driver_links", body)
        self.assertNotIn("INSERT INTO driver_links", body)
        self.assertNotIn("find_driver_link(", body)


if __name__ == "__main__":
    unittest.main()
