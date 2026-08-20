import unittest

from app import available_server_choices, format_account_date, mod_catalog_status, mod_matches_search
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

    def test_mod_catalog_status_supports_paused_and_development_aliases(self):
        paused = Mod("paused", "Paused", "Інше", "", "game", status="призупинено")
        development = Mod("dev", "Dev", "Інше", "", "game", status="in_development")
        self.assertEqual(mod_catalog_status(paused), "paused")
        self.assertEqual(mod_catalog_status(development), "development")
        self.assertEqual(mod_catalog_status(self.mod), "available")

    def test_server_picker_keeps_all_default_servers_and_current_choice(self):
        choices = available_server_choices("", "custom.example", 22100)
        endpoints = {(item["host"], item["port"]) for item in choices}
        self.assertGreaterEqual(len(choices), 8)
        self.assertEqual(len(choices), len(endpoints))
        self.assertIn(("s1.ukraine-gta.com.ua", 22003), endpoints)
        self.assertIn(("s7.ukraine-gta.com.ua", 22003), endpoints)
        self.assertIn(("custom.example", 22100), endpoints)


if __name__ == "__main__":
    unittest.main()
