import unittest
from decimal import Decimal
from unified_fractional_mileage_v2 import accumulate_miles


class FractionalMileageTests(unittest.TestCase):
    def test_fractional_jobs_accumulate_without_losing_miles(self):
        carry = Decimal("0")
        total = 0
        for miles in ("0.40", "0.40", "0.40"):
            award, carry = accumulate_miles(carry, miles)
            total += award
        self.assertEqual(total, 1)
        self.assertEqual(carry, Decimal("0.20"))

    def test_kilometre_conversion_example(self):
        award, carry = accumulate_miles("0", "124.27")
        self.assertEqual((award, carry), (124, Decimal("0.27")))
        award, carry = accumulate_miles(carry, "62.14")
        self.assertEqual((award, carry), (62, Decimal("0.41")))

    def test_bad_inputs_rejected(self):
        for carry, miles in (("-0.1", "2"), ("1", "2"), ("0", "NaN"),
                             ("0", "Infinity"), ("0", "0"), ("0", "-1")):
            with self.subTest(carry=carry, miles=miles):
                with self.assertRaises(ValueError):
                    accumulate_miles(carry, miles)


if __name__ == "__main__":
    unittest.main()
