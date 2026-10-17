"""Unit tests for Steam identity rules; no live provider calls."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import steam_openid_v2


class SteamIdentityTests(unittest.TestCase):
    def test_trusted_exact_match(self):
        steam_id = "76561198000000000"
        self.assertTrue(steam_openid_v2.steam_ids_match(steam_id, steam_id, True))

    def test_untrusted_source_rejected(self):
        steam_id = "76561198000000000"
        self.assertFalse(steam_openid_v2.steam_ids_match(steam_id, steam_id))

    def test_other_steam_account_rejected(self):
        self.assertFalse(steam_openid_v2.steam_ids_match(
            "76561198000000000", "76561198000000001", True
        ))

    def test_malformed_steam_account_rejected(self):
        self.assertFalse(steam_openid_v2.steam_ids_match("209123", "209123", True))

    def test_login_rejects_http(self):
        with self.assertRaises(ValueError):
            steam_openid_v2.make_login_url(
                "http://example.org/auth/steam/callback?state=abc",
                "http://example.org",
            )

    def test_login_rejects_foreign_origin(self):
        with self.assertRaises(ValueError):
            steam_openid_v2.make_login_url(
                "https://attacker.example/auth/steam/callback?state=abc",
                "https://at-transport-ltd.com",
            )


if __name__ == "__main__":
    unittest.main()
