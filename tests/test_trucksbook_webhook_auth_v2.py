import unittest

from trucksbook_webhook_auth_v2 import is_authorized_trucksbook_webhook


class TrucksBookWebhookAuthTests(unittest.TestCase):
    def test_staging_accepts_only_configured_discord_webhook(self):
        self.assertTrue(is_authorized_trucksbook_webhook(
            123456789, isolated_staging=True, configured_id="123456789"
        ))
        self.assertFalse(is_authorized_trucksbook_webhook(
            987654321, isolated_staging=True, configured_id="123456789"
        ))

    def test_staging_fails_closed_when_webhook_is_unconfigured_or_invalid(self):
        for configured in ("", "not-a-number", "0", None):
            with self.subTest(configured=configured):
                self.assertFalse(is_authorized_trucksbook_webhook(
                    123456789, isolated_staging=True, configured_id=configured
                ))

    def test_missing_or_invalid_webhook_id_is_rejected(self):
        for webhook_id in (None, 0, -1, "123456789", True):
            with self.subTest(webhook_id=webhook_id):
                self.assertFalse(is_authorized_trucksbook_webhook(
                    webhook_id, isolated_staging=True, configured_id="123456789"
                ))

    def test_production_keeps_existing_discord_webhook_behavior(self):
        self.assertTrue(is_authorized_trucksbook_webhook(
            123456789, isolated_staging=False, configured_id=""
        ))


if __name__ == "__main__":
    unittest.main()
