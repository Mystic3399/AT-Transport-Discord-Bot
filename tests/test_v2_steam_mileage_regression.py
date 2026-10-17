"""Pure regression tests for Steam identity and unit normalization."""
import unittest

from steam_openid_v2 import make_login_url, steam_ids_match
from unified_distance_units import normalize_distance


class VerificationAndMileageTests(unittest.TestCase):
    def test_steam_identity_requires_trusted_provider(self):
        steam = "76561198000000000"
        self.assertFalse(steam_ids_match(steam, steam, False))
        self.assertTrue(steam_ids_match(steam, steam, True))
        self.assertFalse(steam_ids_match(steam, "76561198000000001", True))

    def test_steam_url_requires_https_callback(self):
        with self.assertRaises(ValueError):
            make_login_url("http://example.test/auth/steam/callback?state=abc",
                           "http://example.test")
        login = make_login_url("https://example.test/auth/steam/callback?state=abc",
                               "https://example.test")
        self.assertIn("steamcommunity.com/openid/login", login)

    def test_both_providers_normalize_km(self):
        from decimal import Decimal
        for provider in ("trucky", "trucksbook"):
            original, unit, miles = normalize_distance("100", "km")
            self.assertEqual(miles, Decimal("62.14"), provider)
            self.assertEqual(unit, "km")


if __name__ == "__main__":
    unittest.main()
