"""Static routing checks for the no-award TrucksBook pending-claim commands."""
import ast
from pathlib import Path
import unittest


BOT_PATH = Path(__file__).resolve().parents[1] / "bot.py"


def command_functions():
    tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    result = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "command"):
                name = next((kw.value.value for kw in decorator.keywords
                             if kw.arg == "name" and isinstance(kw.value, ast.Constant)), None)
                if name:
                    result[name] = node
    return result


class TrucksBookPendingCommandTests(unittest.TestCase):
    def test_driver_command_is_private_and_only_submits_pending_claim(self):
        node = command_functions()["registertrucksbook"]
        source = ast.unparse(node)
        self.assertIn("get_verification_by_channel", source)
        self.assertIn("submit_trucksbook_pending_claim", source)
        self.assertIn("ctx.author.id", source)
        self.assertIn("pending verification", source)
        self.assertNotIn("approve_trucksbook_claim_after_witness", source)
        self.assertNotIn("award_staging_miles", source)
        self.assertNotIn("sync_unified_driver_roles", source)

    def test_staff_approval_uses_witnessed_identity_comparison(self):
        node = command_functions()["approveprovider"]
        source = ast.unparse(node)
        self.assertIn("member_is_verification_staff", source)
        self.assertIn("approve_trucksbook_claim_after_witness", source)
        self.assertIn("confirm-member", source)
        self.assertNotIn("approve_provider_claim", source)


if __name__ == "__main__":
    unittest.main()
