"""Tests for V2 provider distance conversion."""
import unittest
from decimal import Decimal

from distance_normalization_v2 import normalize_distance_miles


class DistanceNormalizationTests(unittest.TestCase):
    def test_km_conversion(self):
        self.assertEqual(normalize_distance_miles(100, "km"), Decimal("62.14"))
        self.assertEqual(normalize_distance_miles("160.9344", "kilometres"), Decimal("100.00"))

    def test_miles_unchanged(self):
        self.assertEqual(normalize_distance_miles("100.25", "miles"), Decimal("100.25"))

    def test_unknown_unit_fails_closed(self):
        for unit in (None, "", "meters", "unknown"):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                normalize_distance_miles(100, unit)

    def test_invalid_values_rejected(self):
        for value in (-1, "NaN", "Infinity", "bad", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_distance_miles(value, "km")

    def test_does_not_round_before_conversion(self):
        self.assertEqual(normalize_distance_miles("1.609344", "km"), Decimal("1.00"))


if __name__ == "__main__":
    unittest.main()
