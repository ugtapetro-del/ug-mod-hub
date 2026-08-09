import unittest

from app import format_account_date, mod_matches_search
from core import Mod


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = Mod(
            "ugta_player_crosshair",
            "UGTA Player Crosshair",
            "Приціл",
            "Змагальний зелений приціл",
            "game/mods/deathmatch/resources",
        )

    def test_search_matches_title_description_category_and_id(self):
        for query in ("player", "зелений", "приціл", "ugta_player"):
            with self.subTest(query=query):
                self.assertTrue(mod_matches_search(self.mod, query))

    def test_search_is_case_insensitive(self):
        self.assertTrue(mod_matches_search(self.mod, "CROSSHAIR"))

    def test_search_rejects_unrelated_text(self):
        self.assertFalse(mod_matches_search(self.mod, "анімація"))

    def test_account_registration_date_is_human_readable(self):
        self.assertEqual(format_account_date("2026-08-07 14:25:00"), "07.08.2026 о 14:25")
        self.assertEqual(format_account_date(""), "дата не вказана")


if __name__ == "__main__":
    unittest.main()
