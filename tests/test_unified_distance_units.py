"""Unit tests for kilometre and mile accounting at provider ingest."""
import unittest
from decimal import Decimal
from unified_distance_units import normalize_distance, normalize_job_distances


class DistanceConversionTests(unittest.TestCase):
    def test_kilometres_to_miles(self):
        self.assertEqual(normalize_distance("100", "km")[2], Decimal("62.14"))
        self.assertEqual(normalize_distance("500", "kilometres")[2], Decimal("310.69"))
        self.assertEqual(normalize_distance("1000", "KM")[2], Decimal("621.37"))

    def test_miles_not_converted_twice(self):
        self.assertEqual(normalize_distance("100", "miles")[2], Decimal("100.00"))

    def test_real_and_race_independent(self):
        record = normalize_job_distances(
            real_distance="100", real_unit="km",
            race_distance="50", race_unit="mi"
        )
        self.assertEqual(record["real_miles"], Decimal("62.14"))
        self.assertEqual(record["race_miles"], Decimal("50.00"))
        self.assertEqual(record["real_original"], Decimal("100"))
        self.assertEqual(record["real_unit"], "km")

    def test_ambiguous_and_invalid_rejected(self):
        for value, unit in ((100, None), ("NaN", "km"), ("Infinity", "mi"),
                            (-1, "km"), (True, "mi"), ("1,000", "km")):
            with self.subTest(value=value, unit=unit):
                with self.assertRaises(ValueError):
                    normalize_distance(value, unit)


if __name__ == "__main__":
    unittest.main()
