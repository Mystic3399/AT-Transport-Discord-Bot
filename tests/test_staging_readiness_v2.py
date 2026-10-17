"""V2 staging readiness gate never passes by default."""
import unittest
from staging_readiness_v2 import report, REQUIRED


class StagingReadinessTests(unittest.TestCase):
    def test_no_evidence_is_not_ready(self):
        r = report({})
        self.assertFalse(r["ready_for_isolated_staging_rehearsal"])
        self.assertTrue(all(x["status"] == "blocked" for x in r["checks"]))

    def test_missing_one_gate_blocks(self):
        env = {name: "YES" for name in REQUIRED}
        env["AT_STEAM_CALLBACK_URL"] = "https://staging.example.test/auth/steam/callback"
        self.assertTrue(report(env)["ready_for_isolated_staging_rehearsal"])
        env["AT_V2_LEDGER_RECONCILIATION_CONFIRMED"] = "NO"
        self.assertFalse(report(env)["ready_for_isolated_staging_rehearsal"])

    def test_insecure_callback_blocks(self):
        env = {name: "YES" for name in REQUIRED}
        env["AT_STEAM_CALLBACK_URL"] = "http://localhost/auth/steam/callback"
        self.assertFalse(report(env)["ready_for_isolated_staging_rehearsal"])


if __name__ == "__main__":
    unittest.main()
