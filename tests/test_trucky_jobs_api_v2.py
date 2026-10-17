import unittest
from unittest.mock import patch
from trucky_jobs_api_v2 import (
    fetch_trucky_jobs_page, inspect_jobs_response_shape,
    TruckyJobLookupUnavailable,
)


class TruckyJobsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_fails_closed(self):
        with patch.dict("os.environ", {"TRUCKY_API_TOKEN": ""}):
            with self.assertRaises(TruckyJobLookupUnavailable):
                await fetch_trucky_jobs_page()

    async def test_invalid_page_rejected(self):
        for page in (0, -1, 101, True, "1"):
            with self.subTest(page=page):
                with self.assertRaises(ValueError):
                    await fetch_trucky_jobs_page(page)

    def test_shape_inspection_does_not_claim_verified_delivery(self):
        response = {
            "data": [{"id": 27805272, "secret": "do-not-display",
                      "driver": {"id": 456, "steam_id": "private"},
                      "game": {"name": "ETS2"}}],
            "current_page": 1,
        }
        result = inspect_jobs_response_shape(response)
        self.assertFalse(result["verified_delivery"])
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["first_record_types"]["id"], "int")
        self.assertEqual(result["first_record_nested_types"]["driver"],
                         {"id": "int", "steam_id": "str"})
        self.assertEqual(result["first_record_nested_types"]["game"],
                         {"name": "str"})
        self.assertNotIn("do-not-display", repr(result))
        self.assertNotIn("private", repr(result))


if __name__ == "__main__":
    unittest.main()
