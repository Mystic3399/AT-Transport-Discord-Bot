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

    def test_legacy_trucksbook_and_v2_links_both_eligible_only_if_active(self):
        from account_unlink import has_valid_tracking_link
        self.assertTrue(callable(has_valid_tracking_link))
        tree = ast.parse(SOURCE)
        names = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef)}
        for function in ("sync_unified_driver_roles", "sync_member_progression_role",
                         "sync_member_permanent_roles", "sync_permanent_system_for_driver"):
            self.assertIn(function, names)
            calls = {n.attr for n in ast.walk(names[function])
                     if isinstance(n, ast.Attribute)}
            self.assertIn("has_valid_tracking_link", calls, function)
        self.assertIn("trucksbook_user_id IS NOT NULL", __import__("inspect").getsource(
            __import__("account_unlink").has_valid_tracking_link))
        self.assertIn("verified_at IS NOT NULL", __import__("inspect").getsource(
            __import__("account_unlink").has_valid_tracking_link))

    def test_unlink_immediately_resyncs_active_ranks_and_weekly_monthly_champions(self):
        tree = ast.parse(SOURCE)
        klass = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                     and n.name == "UnlinkConfirmation")
        fn = next(n for n in ast.walk(klass) if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "confirm_unlink")
        calls = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        self.assertIn("sync_unified_driver_roles", calls)
        self.assertIn("sync_competition_role", calls)

    def test_no_separate_trucky_progression_ladder(self):
        self.assertNotIn("TRUCKY_PROGRESSION_ROLES", SOURCE)


if __name__ == "__main__":
    unittest.main()
