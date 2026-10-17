import asyncio
import unittest
from unittest.mock import patch
from trucky_jobs_probe_v2 import main


class TruckyProbeResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_membership_failure_does_not_crash_job_probe(self):
        job = {"status": "completed", "user_id": 123}
        with patch.dict("os.environ", {"AT_V2_ISOLATED_STAGING": "YES",
                                    "TRUCKY_API_TOKEN": "test-token"}), \
             patch("trucky_jobs_probe_v2.fetch_trucky_jobs_page",
                   new_callable=unittest.mock.AsyncMock,
                   return_value={"data": [job]}), \
             patch("trucky_membership_adapter.inspect_member_identity_fields",
                   new_callable=unittest.mock.AsyncMock,
                   side_effect=RuntimeError("private detail")), \
             patch("builtins.print") as printer:
            await main()
        output = " ".join(str(call) for call in printer.call_args_list)
        self.assertIn("TRUCKY_PROBE_RESULT", output)
        self.assertIn("TRUCKY_MEMBER_IDENTITY_SHAPE", output)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("private detail", output)


if __name__ == "__main__":
    unittest.main()
