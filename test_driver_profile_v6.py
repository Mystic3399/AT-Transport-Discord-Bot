"""Smoke and safety tests for the isolated V6 tacho renderer."""
import unittest
from PIL import Image
from driver_profile_v6 import Account, DriverCard, render_tacho_card

class TachoCardTests(unittest.TestCase):
    def test_mystic_example_is_not_auto_verified(self):
        card = DriverCard(
            display_name="Mystic",
            accounts={
                "Discord": Account("Mystic"),
                "Steam": Account("Mystic"),
                "TrucksBook": Account("Mystic3399"),
                "Trucky": Account("Mystic3399"),
                "Drivers Hub": Account("Mystic"),
            },
        )
        self.assertTrue(all(a.status == "unconfirmed" for a in card.accounts.values()))
        result = render_tacho_card(card)
        self.assertEqual(Image.open(result).size, (1280, 800))

    def test_mileage_must_be_pre_reconciled(self):
        with self.assertRaises(ValueError):
            DriverCard("Driver", real_miles=-1)
        with self.assertRaises(ValueError):
            Account("Driver", status="verified_by_name")

    def test_long_names_and_verified_data_render(self):
        card = DriverCard(
            display_name="Very Long Driver Name " * 12,
            accounts={"Steam": Account("Mystic", "verified")},
            real_miles=12500,
            race_miles=20000,
            rank="Horizon Wolf",
        )
        self.assertGreater(len(render_tacho_card(card).getvalue()), 10000)

if __name__ == "__main__":
    unittest.main()
