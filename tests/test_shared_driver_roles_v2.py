"""Regression checks for shared TrucksBook/Trucky progression role logic."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


class SharedDriverRoleTests(unittest.TestCase):
    def test_one_existing_progression_ladder(self):
        self.assertEqual(SOURCE.count("PROGRESSION_ROLES = ["), 1)
        self.assertIn("async def sync_unified_driver_roles(", SOURCE)
        self.assertIn("return await sync_member_progression_role(guild, discord_user_id, miles)", SOURCE)

    def test_legacy_trucksbook_and_v2_links_both_eligible(self):
        tree = ast.parse(SOURCE)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "sync_unified_driver_roles")
        sql_literals = [n.value for n in ast.walk(fn)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        eligibility = next(s for s in sql_literals if "SELECT EXISTS(" in s)
        self.assertIn("unified_provider_links", eligibility)
        self.assertIn("driver_links", eligibility)
        self.assertIn("verified_at IS NOT NULL", eligibility)

    def test_no_separate_trucky_progression_ladder(self):
        self.assertNotIn("TRUCKY_PROGRESSION_ROLES", SOURCE)


if __name__ == "__main__":
    unittest.main()
