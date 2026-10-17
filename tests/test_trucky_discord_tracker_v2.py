import unittest
from types import SimpleNamespace
from trucky_discord_tracker_v2 import parse_trucky_embed, inspect_trucky_message, TRACKER_CHANNEL_ID


class TruckyDiscordReaderTests(unittest.TestCase):
    def setUp(self):
        self.embed = {
            "title": "Job Completed - #27805272",
            "author": {"name": "QUIETGHOST"},
            "description": "Oslo to Hamar\\n129 mi.",
            "fields": [{"name": "Stats Type", "value": "Real Miles"}],
        }

    def test_read_only_parse(self):
        result = parse_trucky_embed(self.embed)
        self.assertEqual(result["provider_job_id"], "27805272")
        self.assertEqual(result["distance"], "129")
        self.assertEqual(result["mileage_type"], "real")
        self.assertFalse(result["trusted"])

    def test_no_source_authentication_no_event(self):
        msg = SimpleNamespace(
            channel=SimpleNamespace(id=TRACKER_CHANNEL_ID),
            webhook_id=42, embeds=[self.embed],
        )
        self.assertIsNone(inspect_trucky_message(msg, expected_webhook_id=0))
        self.assertIsNone(inspect_trucky_message(msg, expected_webhook_id=43))
        self.assertEqual(inspect_trucky_message(msg, expected_webhook_id=42)["provider_job_id"], "27805272")

    def test_emoji_stats_type(self):
        self.embed["fields"] = [{"name": "🚀 Stats Type", "value": "Real Miles"}]
        self.assertEqual(parse_trucky_embed(self.embed)["mileage_type"], "real")

    def test_race_miles_remain_separate(self):
        self.embed["fields"] = [{"name": "🚀 Stats Type", "value": "Race Miles"}]
        self.assertEqual(parse_trucky_embed(self.embed)["mileage_type"], "race")

    def test_distance_miles_word(self):
        self.embed["description"] = "Oslo to Hamar: 129 miles"
        self.assertEqual(parse_trucky_embed(self.embed)["distance_unit"], "mi")

    def test_distance_kilometres(self):
        self.embed["description"] = "Oslo to Hamar: 207.61 kilometres"
        result = parse_trucky_embed(self.embed)
        self.assertEqual(result["distance_unit"], "km")
        self.assertEqual(result["distance"], "207.61")

    def test_job_id_must_be_in_title(self):
        self.embed["title"] = "Trucky update"
        self.embed["description"] = "Job Completed - #27805272; 129 mi"
        self.assertIsNone(parse_trucky_embed(self.embed))

    def test_dedicated_distance_field(self):
        self.embed["description"] = "Oslo to Hamar"
        self.embed["fields"].append({"name": "Distance", "value": "129 mi"})
        self.assertEqual(parse_trucky_embed(self.embed)["distance"], "129")

    def test_reject_multiple_distance_fields(self):
        self.embed["fields"].extend([
            {"name": "Distance", "value": "129 mi"},
            {"name": "Total Distance", "value": "129 mi"},
        ])
        self.assertIsNone(parse_trucky_embed(self.embed))

    def test_reject_ambiguous_distance(self):
        self.embed["description"] = "129 mi. and 20 km"
        self.assertIsNone(parse_trucky_embed(self.embed))

    def test_reject_missing_stats(self):
        self.embed["fields"] = []
        self.assertIsNone(parse_trucky_embed(self.embed))


if __name__ == "__main__":
    unittest.main()
