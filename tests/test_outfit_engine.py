from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from outfit_engine import StateStore


class OutfitEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_profile(self) -> None:
        self.store.save_profile(
            {
                "location": "Baltimore, MD",
                "style_keywords": ["sporty", "minimal"],
                "comfort_preferences": ["breathable", "soft"],
                "lifestyle": "active",
                "repeat_tolerance_days": 4,
                "default_formality": "casual",
            }
        )

    def _seed_wardrobe(self) -> None:
        self.store.upsert_wardrobe_items(
            [
                {"name": "Black Tee", "category": "top", "source": "manual", "status": "confirmed"},
                {"name": "White Tee", "category": "top", "source": "manual", "status": "confirmed"},
                {"name": "Grey Hoodie", "category": "top", "source": "manual", "status": "confirmed"},
                {"name": "Dark Jeans", "category": "bottom", "source": "manual", "status": "confirmed"},
                {"name": "Black Joggers", "category": "bottom", "source": "manual", "status": "confirmed"},
                {"name": "Khaki Shorts", "category": "bottom", "source": "manual", "status": "confirmed"},
                {"name": "White Sneakers", "category": "shoes", "source": "manual", "status": "confirmed"},
                {"name": "Black Boots", "category": "shoes", "source": "manual", "status": "confirmed"},
                {"name": "Blue Rain Jacket", "category": "outerwear", "source": "manual", "status": "confirmed"},
            ]
        )

    def test_onboarding_persists_profile_and_wardrobe(self) -> None:
        self._seed_profile()
        self._seed_wardrobe()
        snapshot = self.store.get_state_snapshot()
        self.assertTrue(snapshot["onboarding_complete"])
        self.assertEqual(snapshot["counts"]["confirmed"], 9)
        self.assertEqual(self.store.load_profile()["location"], "Baltimore, MD")

    def test_email_ingestion_deduplicates_pending_items(self) -> None:
        extracted = self.store.extract_clothing_items(
            subject="Your order: Black Linen Shirt and White Sneakers",
            body="Items in this order: Black Linen Shirt. White Sneakers.",
            sender="shop@example.com",
            gmail_message_id="msg-1",
        )
        self.store.upsert_wardrobe_items(extracted["items"])
        self.store.upsert_wardrobe_items(extracted["items"])
        pending = self.store.list_pending_items()
        self.assertEqual(pending["count"], 2)
        self.assertTrue(all(item["status"] == "pending" for item in pending["items"]))

    def test_hot_cold_and_rainy_shortlists(self) -> None:
        self._seed_profile()
        self._seed_wardrobe()
        hot = self.store.generate_outfit_options(
            {
                "ok": True,
                "location": "Baltimore",
                "date": "2026-04-19",
                "high_f": 86,
                "low_f": 72,
                "precipitation_probability": 5,
                "weather_code": 1,
            }
        )
        cold = self.store.generate_outfit_options(
            {
                "ok": True,
                "location": "Baltimore",
                "date": "2026-04-20",
                "high_f": 44,
                "low_f": 34,
                "precipitation_probability": 10,
                "weather_code": 3,
            }
        )
        rainy = self.store.generate_outfit_options(
            {
                "ok": True,
                "location": "Baltimore",
                "date": "2026-04-21",
                "high_f": 62,
                "low_f": 55,
                "precipitation_probability": 80,
                "weather_code": 63,
            }
        )
        self.assertEqual(len(hot["options"]), 3)
        self.assertEqual(len(cold["options"]), 3)
        self.assertEqual(len(rainy["options"]), 3)
        self.assertTrue(all(option["items"] for option in hot["options"]))
        self.assertTrue(any("rain" in option["summary_text"].lower() for option in rainy["options"]))

    def test_feedback_updates_wear_history_and_reduces_repeats(self) -> None:
        self._seed_profile()
        self._seed_wardrobe()
        morning = self.store.generate_outfit_options(
            {
                "ok": True,
                "location": "Baltimore",
                "date": "2026-04-19",
                "high_f": 70,
                "low_f": 58,
                "precipitation_probability": 15,
                "weather_code": 2,
            }
        )
        self.store.record_recommendation(morning)
        chosen_ids = {item["id"] for item in morning["options"][0]["items"]}
        feedback = self.store.record_feedback(1)
        self.assertTrue(feedback["ok"])
        next_run = self.store.generate_outfit_options(
            {
                "ok": True,
                "location": "Baltimore",
                "date": "2026-04-20",
                "high_f": 70,
                "low_f": 58,
                "precipitation_probability": 15,
                "weather_code": 2,
            }
        )
        next_ids = {item["id"] for item in next_run["options"][0]["items"]}
        self.assertNotEqual(chosen_ids, next_ids)

    def test_invalid_feedback_does_not_corrupt_history(self) -> None:
        self._seed_profile()
        self._seed_wardrobe()
        morning = self.store.generate_outfit_options(
            {
                "ok": True,
                "location": "Baltimore",
                "date": "2026-04-19",
                "high_f": 68,
                "low_f": 56,
                "precipitation_probability": 10,
                "weather_code": 2,
            }
        )
        self.store.record_recommendation(morning)
        feedback = self.store.record_feedback("tomorrow")
        self.assertFalse(feedback["ok"])
        latest = self.store.load_recommendations()[-1]
        self.assertIsNone(latest["selected_option"])

    def test_weather_failure_falls_back_to_generic_logic(self) -> None:
        self._seed_profile()
        self._seed_wardrobe()
        response = self.store.generate_outfit_options({"ok": False, "location": "Baltimore"})
        self.assertTrue(response["ok"])
        self.assertTrue(response["weather"]["used_fallback"])
        self.assertIn("fallback", response["message_text"].lower())


if __name__ == "__main__":
    unittest.main()
