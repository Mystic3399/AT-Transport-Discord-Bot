import unittest

from trucksbook_webhook_auth_v2 import (
    inspect_staging_trucksbook_delivery,
    is_authorized_trucksbook_webhook,
)


class TrucksBookWebhookAuthorizationTests(unittest.TestCase):
    def test_staging_accepts_only_configured_discord_webhook(self):
        self.assertTrue(is_authorized_trucksbook_webhook(
            123456789, isolated_staging=True, configured_id="123456789"
        ))
        self.assertFalse(is_authorized_trucksbook_webhook(
            987654321, isolated_staging=True, configured_id="123456789"
        ))

    def test_staging_requires_configured_source_and_expected_guild(self):
        allowed = inspect_staging_trucksbook_delivery(
            webhook_id=123456789,
            guild_id=111,
            channel_id=222,
            configured_webhook_id="123456789",
            configured_guild_id=111,
        )
        self.assertEqual(allowed, {
            "source_match": True,
            "guild_match": True,
            "channel_status": "not_configured",
            "allowed": True,
        })
        wrong_source = inspect_staging_trucksbook_delivery(
            webhook_id=123456788,
            guild_id=111,
            channel_id=222,
            configured_webhook_id="123456789",
            configured_guild_id=111,
        )
        self.assertFalse(wrong_source["allowed"])
        self.assertFalse(wrong_source["source_match"])
        wrong_guild = inspect_staging_trucksbook_delivery(
            webhook_id=123456789,
            guild_id=112,
            channel_id=222,
            configured_webhook_id="123456789",
            configured_guild_id=111,
        )
        self.assertFalse(wrong_guild["allowed"])
        self.assertFalse(wrong_guild["guild_match"])

    def test_optional_staging_channel_is_enforced_when_configured(self):
        kwargs = dict(
            webhook_id=123456789,
            guild_id=111,
            channel_id=222,
            configured_webhook_id="123456789",
            configured_guild_id=111,
            configured_channel_id="222",
        )
        self.assertTrue(inspect_staging_trucksbook_delivery(**kwargs)["allowed"])
        kwargs["channel_id"] = 223
        mismatch = inspect_staging_trucksbook_delivery(**kwargs)
        self.assertEqual(mismatch["channel_status"], "mismatch")
        self.assertFalse(mismatch["allowed"])

    def test_malformed_or_oversized_configuration_fails_closed(self):
        for config in ("", "not-a-number", "0", None, "１２３", "1" * 21):
            with self.subTest(config=config):
                self.assertFalse(is_authorized_trucksbook_webhook(
                    123456789, isolated_staging=True, configured_id=config
                ))
        for webhook_id in (None, 0, -1, "123456789", True):
            with self.subTest(webhook_id=webhook_id):
                self.assertFalse(is_authorized_trucksbook_webhook(
                    webhook_id, isolated_staging=True, configured_id="123456789"
                ))
        invalid_channel = inspect_staging_trucksbook_delivery(
            webhook_id=123456789,
            guild_id=111,
            channel_id=222,
            configured_webhook_id="123456789",
            configured_guild_id=111,
            configured_channel_id="invalid",
        )
        self.assertEqual(invalid_channel["channel_status"], "invalid_configuration")
        self.assertFalse(invalid_channel["allowed"])

    def test_production_keeps_existing_discord_webhook_behavior(self):
        self.assertTrue(is_authorized_trucksbook_webhook(
            123456789, isolated_staging=False, configured_id=""
        ))


if __name__ == "__main__":
    unittest.main()
