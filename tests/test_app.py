from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
from outfit_engine import StateStore


class AppBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = app.store
        self.original_embedded_prefs = app.EMBEDDED_DEMO_PREFS
        self.original_embedded_config = app.EMBEDDED_DEMO_CONFIG
        self.original_forecast_fetch = app._fetch_target_date_forecast
        app.store = StateStore(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        app.store = self.original_store
        app.EMBEDDED_DEMO_PREFS = self.original_embedded_prefs
        app.EMBEDDED_DEMO_CONFIG = self.original_embedded_config
        app._fetch_target_date_forecast = self.original_forecast_fetch
        self.temp_dir.cleanup()

    def test_autonomous_run_guard_blocks_when_not_onboarded(self) -> None:
        result = app.autonomous_run_guard()
        self.assertTrue(result["ok"])
        self.assertFalse(result["ready"])
        self.assertIn("Open this agent in Ara chat", result["message_text"])

    def test_autonomous_run_guard_allows_onboarded_state(self) -> None:
        app.store.save_profile(
            {
                "location": "Baltimore, MD",
                "style_keywords": ["minimal"],
                "comfort_preferences": ["soft"],
                "lifestyle": "active",
                "repeat_tolerance_days": 3,
                "default_formality": "casual",
            }
        )
        app.store.upsert_wardrobe_items(
            [
                {"name": "Black Tee", "category": "top", "source": "manual", "status": "confirmed"},
                {"name": "White Tee", "category": "top", "source": "manual", "status": "confirmed"},
                {"name": "Dark Jeans", "category": "bottom", "source": "manual", "status": "confirmed"},
                {"name": "Black Joggers", "category": "bottom", "source": "manual", "status": "confirmed"},
                {"name": "White Sneakers", "category": "shoes", "source": "manual", "status": "confirmed"},
                {"name": "Blue Rain Jacket", "category": "outerwear", "source": "manual", "status": "confirmed"},
            ]
        )
        result = app.autonomous_run_guard()
        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])

    def test_run_demo_daily_brief_seeds_and_generates_options(self) -> None:
        app.EMBEDDED_DEMO_PREFS = {
            "location": "Baltimore, Maryland",
            "style": "formal, minimal",
            "comfort_preferences": "fitted, soft",
            "lifestyle": "professional",
            "repeat_tolerance": "once a week",
            "wardrobe_items": [
                {"name": "White Button Down", "category": "top"},
                {"name": "Blue Oxford Shirt", "category": "top"},
                {"name": "Black Trousers", "category": "bottom"},
                {"name": "Navy Trousers", "category": "bottom"},
                {"name": "Black Loafers", "category": "shoes"},
                {"name": "White Sneakers", "category": "shoes"},
                {"name": "Navy Blazer", "category": "outerwear"},
            ],
        }
        app.EMBEDDED_DEMO_CONFIG = {"target_date": "2026-04-20", "label": "test"}

        def fake_forecast(location: str, target_date: str) -> dict[str, object]:
            return {
                "ok": True,
                "location": location,
                "date": target_date,
                "high_f": 67,
                "low_f": 51,
                "precipitation_probability": 20,
                "weather_code": 2,
            }

        app._fetch_target_date_forecast = fake_forecast
        result = app.run_demo_daily_brief()
        self.assertTrue(result["ok"])
        self.assertTrue(result["seeded"])
        self.assertEqual(result["target_date"], "2026-04-20")
        self.assertEqual(len(result["options"]), 3)
        self.assertIn("2026-04-20", result["message_text"])


if __name__ == "__main__":
    unittest.main()
