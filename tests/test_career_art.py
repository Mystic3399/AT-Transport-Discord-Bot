import unittest
from io import BytesIO

from PIL import Image

import career_art


class CareerArtTests(unittest.TestCase):
    def assert_png(self, payload, size):
        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, size)
            image.load()

    def test_standings_render_full_partial_and_empty_boards(self):
        rows = [
            (f"Driver {position}", 15000 - position * 100, position == 4)
            for position in range(1, 11)
        ]
        for period, entries in (("weekly", rows), ("monthly", rows[:2]), ("all-time", [])):
            with self.subTest(period=period):
                self.assert_png(career_art.standings(period, entries), (1121, 1403))

    def test_recognition_art_uses_existing_assets(self):
        self.assert_png(career_art.hall_of_fame({"Immortal": ["Driver"], "Elite": ["A", "B"]}), (1122, 1402))
        self.assert_png(career_art.achievement("Driver", "Viking Pathfinder", 150000), (1536, 1024))
        self.assert_png(career_art.achievement(
            "Driver", "100K Centurion Club", 100000, permanent=True
        ), (1536, 1024))
