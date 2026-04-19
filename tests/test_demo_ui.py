from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import demo_ui


class DemoUiTests(unittest.TestCase):
    def test_seed_local_state_creates_onboarded_snapshot(self) -> None:
        preferences = {
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
                {"name": "Camel Trench Coat", "category": "outerwear"},
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = demo_ui.seed_local_state(preferences, Path(temp_dir))
        self.assertTrue(snapshot["onboarding_complete"])
        self.assertEqual(snapshot["counts"]["confirmed"], 6)

    def test_embed_demo_payloads_updates_markers(self) -> None:
        source = """
# BEGIN_EMBEDDED_DEMO_PREFS
EMBEDDED_DEMO_PREFS = json.loads(
    r\"\"\"{}\"\"\"
)
# END_EMBEDDED_DEMO_PREFS
# BEGIN_EMBEDDED_DEMO_CONFIG
EMBEDDED_DEMO_CONFIG = json.loads(
    r\"\"\"{"target_date": "", "label": "Wardrobe demo run"}\"\"\"
)
# END_EMBEDDED_DEMO_CONFIG
""".strip()
        preferences = {"location": "Baltimore", "wardrobe_items": [{"name": "Black Tee", "category": "top"}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            app_path = Path(temp_dir) / "app.py"
            app_path.write_text(source, encoding="utf-8")
            demo_ui.embed_demo_payloads(preferences, "2026-04-20", app_path=app_path)
            updated = app_path.read_text(encoding="utf-8")
        self.assertIn("2026-04-20", updated)
        self.assertIn("Baltimore", updated)
        self.assertIn(json.dumps(preferences, indent=2), updated)


if __name__ == "__main__":
    unittest.main()
