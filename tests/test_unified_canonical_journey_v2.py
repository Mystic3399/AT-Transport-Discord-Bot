import unittest
from unified_canonical_journey_v2 import canonical_delivery_key

class CanonicalJourneyTests(unittest.TestCase):
    def test_provider_ids_are_distinct(self):
        self.assertNotEqual(canonical_delivery_key("trucky", "123"),
                            canonical_delivery_key("trucksbook", "123"))

    def test_verified_shared_identity(self):
        self.assertEqual(
            canonical_delivery_key("trucky", "a", shared_journey_id="trip-7", shared_id_verified=True),
            canonical_delivery_key("trucksbook", "b", shared_journey_id="trip-7", shared_id_verified=True))

    def test_unverified_identity_rejected(self):
        with self.assertRaises(ValueError):
            canonical_delivery_key("trucky", "a", shared_journey_id="trip-7")

    def test_invalid_identity_rejected(self):
        with self.assertRaises(ValueError):
            canonical_delivery_key("trucky", "a", shared_journey_id="invalid id", shared_id_verified=True)

if __name__ == "__main__":
    unittest.main()
