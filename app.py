from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

try:
    import ara_sdk as ara
except ImportError:  # pragma: no cover - local shim for tests and static validation.
    class _AraShim:
        @staticmethod
        def tool(func=None, **_kwargs):
            if func is None:
                def decorator(inner):
                    return inner
                return decorator
            return func

        @staticmethod
        def Automation(name: str, **kwargs):
            return {"name": name, **kwargs}

        @staticmethod
        def env(name: str, default: Any = None):
            return os.getenv(name, default)

        @staticmethod
        def secret(name: str):
            return os.getenv(name)

    ara = _AraShim()

try:
    from outfit_engine import StateStore
except ModuleNotFoundError as exc:
    if exc.name != "outfit_engine":
        raise

    # Ara's documented quickstart deploys a single `app.py` entry file.
    # Keep a self-contained fallback here so cloud deploys still work even if
    # sibling local modules are not uploaded alongside the entrypoint.
    import json
    import re
    import urllib.parse
    import urllib.request
    import uuid
    from datetime import datetime, timedelta, timezone
    from itertools import product
    from pathlib import Path

    TOP_CATEGORIES = {"top", "bottom", "shoes", "outerwear", "one_piece", "accessory"}
    DEFAULT_PROFILE = {
        "location": "",
        "style_keywords": [],
        "comfort_preferences": [],
        "lifestyle": "sedentary",
        "repeat_tolerance_days": 3,
        "default_formality": "casual",
    }
    DEFAULT_PREFERENCES = {
        "colors": {},
        "categories": {},
        "style_tags": {},
    }
    DEFAULT_META = {
        "latest_recommendation_id": None,
        "processed_gmail_message_ids": [],
    }

    CATEGORY_KEYWORDS = {
        "top": [
            "shirt",
            "tee",
            "t-shirt",
            "t shirt",
            "blouse",
            "sweater",
            "hoodie",
            "cardigan",
            "tank",
            "polo",
            "button-down",
            "button down",
            "pullover",
            "crewneck",
            "top",
        ],
        "bottom": [
            "jeans",
            "pants",
            "trousers",
            "leggings",
            "joggers",
            "shorts",
            "skirt",
            "chinos",
            "slacks",
            "bottom",
        ],
        "shoes": [
            "sneakers",
            "shoes",
            "boots",
            "loafers",
            "sandals",
            "heels",
            "trainers",
            "flats",
        ],
        "outerwear": [
            "jacket",
            "coat",
            "blazer",
            "parka",
            "raincoat",
            "windbreaker",
            "outerwear",
            "overshirt",
        ],
        "one_piece": [
            "dress",
            "jumpsuit",
            "romper",
            "one-piece",
            "one piece",
        ],
        "accessory": [
            "hat",
            "cap",
            "scarf",
            "bag",
            "belt",
            "jewelry",
            "socks",
            "accessory",
        ],
    }

    COLOR_WORDS = {
        "black",
        "white",
        "gray",
        "grey",
        "blue",
        "navy",
        "green",
        "olive",
        "red",
        "burgundy",
        "pink",
        "purple",
        "brown",
        "tan",
        "beige",
        "cream",
        "yellow",
        "orange",
    }

    MATERIAL_WORDS = {
        "cotton",
        "linen",
        "denim",
        "wool",
        "fleece",
        "silk",
        "leather",
        "polyester",
        "nylon",
        "cashmere",
    }

    COMFORT_TAG_RULES = {
        "breathable": ["linen", "cotton", "tank", "shorts", "tee", "t-shirt", "t shirt"],
        "warm": ["wool", "fleece", "sweater", "hoodie", "coat", "jacket", "boots"],
        "stretchy": ["leggings", "joggers", "athletic", "spandex"],
        "soft": ["cashmere", "fleece", "cotton", "sweater", "hoodie"],
        "rain_ready": ["raincoat", "boots", "windbreaker"],
    }

    STYLE_TAG_RULES = {
        "sporty": ["sneakers", "leggings", "joggers", "hoodie", "athletic"],
        "classic": ["button-down", "button down", "blazer", "loafers", "trousers"],
        "minimal": ["black", "white", "neutral", "beige", "cream"],
        "streetwear": ["hoodie", "joggers", "oversized", "sneakers"],
        "polished": ["blazer", "dress", "heels", "slacks"],
        "relaxed": ["tee", "sweatshirt", "joggers", "linen", "cardigan"],
    }

    FORMALITY_RULES = {
        "formal": ["blazer", "heels", "slacks", "dress shoes", "suit"],
        "smart-casual": ["button-down", "button down", "loafers", "dress", "trousers"],
        "casual": ["tee", "hoodie", "jeans", "shorts", "sneakers", "leggings"],
    }

    WEATHER_CODE_SUMMARIES = {
        0: "clear",
        1: "mostly clear",
        2: "partly cloudy",
        3: "cloudy",
        45: "foggy",
        48: "foggy",
        51: "light drizzle",
        53: "drizzle",
        55: "heavy drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        71: "light snow",
        73: "snow",
        75: "heavy snow",
        80: "rain showers",
        81: "rain showers",
        82: "heavy rain showers",
        95: "thunderstorm",
    }

    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _iso_now() -> str:
        return _utc_now().isoformat()

    def _ensure_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = [part.strip() for part in re.split(r"[,\n/]+", value) if part.strip()]
            return parts
        if isinstance(value, list):
            return [str(part).strip() for part in value if str(part).strip()]
        return [str(value).strip()]

    def _normalize_formality(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"formal", "smart-casual", "smart casual", "casual"}:
            return text.replace("smart casual", "smart-casual")
        return "casual"

    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(row, sort_keys=True) for row in rows)
        if body:
            body = f"{body}\n"
        path.write_text(body, encoding="utf-8")

    def _normalize_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _slugify(value: str) -> str:
        text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return text or uuid.uuid4().hex[:8]

    def infer_category(text: str) -> str:
        lowered = text.lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return category
        return "top"

    def infer_color(text: str) -> str:
        lowered = text.lower()
        for color in COLOR_WORDS:
            if color in lowered:
                return "gray" if color == "grey" else color
        return ""

    def infer_material(text: str) -> str:
        lowered = text.lower()
        for material in MATERIAL_WORDS:
            if material in lowered:
                return material
        return ""

    def infer_formality(text: str) -> str:
        lowered = text.lower()
        for formality, keywords in FORMALITY_RULES.items():
            if any(keyword in lowered for keyword in keywords):
                return formality
        return "casual"

    def infer_style_tags(text: str) -> list[str]:
        lowered = text.lower()
        tags = [
            tag
            for tag, keywords in STYLE_TAG_RULES.items()
            if any(keyword in lowered for keyword in keywords)
        ]
        return sorted(set(tags))

    def infer_comfort_tags(text: str) -> list[str]:
        lowered = text.lower()
        tags = [
            tag
            for tag, keywords in COMFORT_TAG_RULES.items()
            if any(keyword in lowered for keyword in keywords)
        ]
        return sorted(set(tags))

    def infer_warmth(name: str, category: str, material: str = "") -> int:
        lowered = name.lower()
        warmth = 1
        if category in {"outerwear"}:
            warmth = 3
        elif category in {"shoes"}:
            warmth = 1
        elif any(token in lowered for token in ["hoodie", "sweater", "boots", "wool", "fleece"]):
            warmth = 2
        elif any(token in lowered for token in ["coat", "parka", "puffer"]):
            warmth = 3
        elif any(token in lowered for token in ["shorts", "tank", "sandals", "linen"]):
            warmth = 0
        if material in {"wool", "cashmere", "fleece", "leather"}:
            warmth = min(3, warmth + 1)
        if material in {"linen", "silk"}:
            warmth = max(0, warmth - 1)
        return warmth

    def infer_active_ok(name: str, category: str) -> bool:
        lowered = name.lower()
        if category in {"shoes"} and any(token in lowered for token in ["sneakers", "trainers", "boots"]):
            return True
        return any(token in lowered for token in ["athletic", "leggings", "joggers", "hoodie", "tee", "shorts"])

    def infer_metadata(name: str, category: str = "") -> dict[str, Any]:
        resolved_category = category or infer_category(name)
        material = infer_material(name)
        return {
            "category": resolved_category,
            "color": infer_color(name),
            "material": material,
            "warmth": infer_warmth(name, resolved_category, material),
            "formality": infer_formality(name),
            "comfort_tags": infer_comfort_tags(name),
            "style_tags": infer_style_tags(name),
            "active_ok": infer_active_ok(name, resolved_category),
        }

    def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
        payload = {**DEFAULT_PROFILE, **(profile or {})}
        payload["location"] = _normalize_text(payload.get("location"))
        payload["style_keywords"] = _ensure_list(payload.get("style_keywords"))
        payload["comfort_preferences"] = _ensure_list(payload.get("comfort_preferences"))
        payload["lifestyle"] = str(payload.get("lifestyle") or "sedentary").strip().lower()
        payload["repeat_tolerance_days"] = max(1, int(payload.get("repeat_tolerance_days") or 3))
        payload["default_formality"] = _normalize_formality(payload.get("default_formality"))
        return payload

    def _normalize_item(item: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = {**(existing or {}), **(item or {})}
        name = _normalize_text(merged.get("name"))
        inferred = infer_metadata(name, category=str(merged.get("category") or ""))
        category = str(merged.get("category") or inferred["category"]).strip().lower()
        category = category if category in TOP_CATEGORIES else infer_category(name)
        source = str(merged.get("source") or "manual").strip().lower()
        status = str(merged.get("status") or ("pending" if source == "gmail" else "confirmed")).strip().lower()
        comfort_tags = _ensure_list(merged.get("comfort_tags")) or inferred["comfort_tags"]
        style_tags = _ensure_list(merged.get("style_tags")) or inferred["style_tags"]
        item_id = str(merged.get("id") or _slugify(f"{name}-{category}-{uuid.uuid4().hex[:6]}"))
        return {
            "id": item_id,
            "name": name or "Unnamed item",
            "category": category,
            "color": str(merged.get("color") or inferred["color"] or "").strip().lower(),
            "material": str(merged.get("material") or inferred["material"] or "").strip().lower(),
            "warmth": max(0, min(3, int(merged.get("warmth", inferred["warmth"])))),
            "formality": _normalize_formality(merged.get("formality") or inferred["formality"]),
            "comfort_tags": comfort_tags,
            "style_tags": style_tags,
            "active_ok": bool(
                merged["active_ok"] if "active_ok" in merged else inferred["active_ok"]
            ),
            "source": source,
            "status": "confirmed" if status == "confirmed" else "pending",
            "last_worn_at": merged.get("last_worn_at"),
            "times_worn": max(0, int(merged.get("times_worn") or 0)),
            "gmail_message_id": _normalize_text(merged.get("gmail_message_id")),
        }

    def _item_signature(item: dict[str, Any]) -> str:
        return "|".join(
            [
                item.get("gmail_message_id") or "",
                item.get("category") or "",
                _normalize_text(item.get("name")).lower(),
                item.get("source") or "",
            ]
        )

    def _weather_summary(code: int) -> str:
        return WEATHER_CODE_SUMMARIES.get(int(code), "mixed weather")

    def _normalize_forecast(forecast: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(forecast or {})
        if not payload.get("ok"):
            return {
                "ok": False,
                "location": payload.get("location") or "",
                "date": payload.get("date") or _utc_now().date().isoformat(),
                "high_f": 72,
                "low_f": 60,
                "precipitation_probability": 10,
                "weather_code": 2,
                "summary": "mild and partly cloudy",
                "conditions": "mild",
                "needs_outerwear": False,
                "used_fallback": True,
            }
        high_f = int(round(float(payload.get("high_f", 72))))
        low_f = int(round(float(payload.get("low_f", 60))))
        avg_f = (high_f + low_f) / 2
        precipitation = int(round(float(payload.get("precipitation_probability", 0))))
        weather_code = int(payload.get("weather_code", 2))
        conditions = "mild"
        if precipitation >= 50 or weather_code in {61, 63, 65, 80, 81, 82, 95}:
            conditions = "rainy"
        elif avg_f <= 48:
            conditions = "cold"
        elif avg_f >= 78:
            conditions = "hot"
        return {
            "ok": True,
            "location": payload.get("location") or "",
            "date": payload.get("date") or _utc_now().date().isoformat(),
            "high_f": high_f,
            "low_f": low_f,
            "precipitation_probability": precipitation,
            "weather_code": weather_code,
            "summary": payload.get("summary") or _weather_summary(weather_code),
            "conditions": conditions,
            "needs_outerwear": precipitation >= 50 or avg_f < 60,
            "used_fallback": False,
        }

    def _fetch_json(url: str, timeout: int = 20) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "ara-hackathon-outfit-agent/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
        data = json.loads(payload) if payload else {}
        return data if isinstance(data, dict) else {}

    def _format_item_brief(item: dict[str, Any]) -> str:
        return f"{item.get('name')} ({item.get('category')})"

    class StateStore:
        def __init__(self, base_dir: str | Path | None = None) -> None:
            root = Path(base_dir) if base_dir else Path(__file__).resolve().parent / "state"
            self.base_dir = root
            self.profile_path = root / "profile.json"
            self.wardrobe_path = root / "wardrobe.json"
            self.preferences_path = root / "preferences.json"
            self.recommendations_path = root / "recommendations.jsonl"
            self.meta_path = root / "meta.json"
            self.base_dir.mkdir(parents=True, exist_ok=True)

        def load_profile(self) -> dict[str, Any]:
            return _normalize_profile(_read_json(self.profile_path, DEFAULT_PROFILE))

        def save_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
            normalized = _normalize_profile(profile)
            _write_json(self.profile_path, normalized)
            return {
                "ok": True,
                "profile": normalized,
                "message_text": (
                    f"Saved your profile for {normalized['location'] or 'an unspecified location'} "
                    f"with {len(normalized['style_keywords'])} style keywords."
                ),
            }

        def load_wardrobe(self) -> list[dict[str, Any]]:
            rows = _read_json(self.wardrobe_path, [])
            if not isinstance(rows, list):
                return []
            return [_normalize_item(row) for row in rows if isinstance(row, dict)]

        def save_wardrobe(self, items: list[dict[str, Any]]) -> None:
            _write_json(self.wardrobe_path, items)

        def load_preferences(self) -> dict[str, Any]:
            payload = _read_json(self.preferences_path, DEFAULT_PREFERENCES)
            if not isinstance(payload, dict):
                payload = dict(DEFAULT_PREFERENCES)
            merged = {**DEFAULT_PREFERENCES, **payload}
            for key in DEFAULT_PREFERENCES:
                if not isinstance(merged.get(key), dict):
                    merged[key] = {}
            return merged

        def save_preferences(self, preferences: dict[str, Any]) -> None:
            _write_json(self.preferences_path, preferences)

        def load_meta(self) -> dict[str, Any]:
            payload = _read_json(self.meta_path, DEFAULT_META)
            if not isinstance(payload, dict):
                payload = dict(DEFAULT_META)
            merged = {**DEFAULT_META, **payload}
            merged["processed_gmail_message_ids"] = list(
                dict.fromkeys(_ensure_list(merged.get("processed_gmail_message_ids")))
            )
            return merged

        def save_meta(self, meta: dict[str, Any]) -> None:
            _write_json(self.meta_path, meta)

        def load_recommendations(self) -> list[dict[str, Any]]:
            return _read_jsonl(self.recommendations_path)

        def save_recommendations(self, rows: list[dict[str, Any]]) -> None:
            _write_jsonl(self.recommendations_path, rows)

        def get_state_snapshot(self) -> dict[str, Any]:
            profile = self.load_profile()
            wardrobe = self.load_wardrobe()
            pending = [item for item in wardrobe if item["status"] == "pending"]
            confirmed = [item for item in wardrobe if item["status"] == "confirmed"]
            meta = self.load_meta()
            latest_recommendation = meta.get("latest_recommendation_id")
            onboarded = bool(profile.get("location") and len(confirmed) >= 6)
            return {
                "ok": True,
                "profile": profile,
                "counts": {
                    "wardrobe_total": len(wardrobe),
                    "confirmed": len(confirmed),
                    "pending": len(pending),
                },
                "latest_recommendation_id": latest_recommendation,
                "onboarding_complete": onboarded,
                "message_text": (
                    f"Profile location: {profile.get('location') or 'missing'}. "
                    f"Confirmed wardrobe items: {len(confirmed)}. Pending items: {len(pending)}."
                ),
            }

        def upsert_wardrobe_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
            existing = self.load_wardrobe()
            meta = self.load_meta()
            by_id = {row["id"]: row for row in existing}
            by_signature = {_item_signature(row): row for row in existing}
            added: list[dict[str, Any]] = []
            updated: list[dict[str, Any]] = []

            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                normalized = _normalize_item(raw_item, existing=by_id.get(str(raw_item.get("id") or "")))
                signature = _item_signature(normalized)
                target = by_id.get(normalized["id"]) or by_signature.get(signature)
                if target:
                    target.update(normalized)
                    updated.append(target)
                else:
                    existing.append(normalized)
                    by_id[normalized["id"]] = normalized
                    by_signature[signature] = normalized
                    added.append(normalized)
                if normalized.get("gmail_message_id"):
                    meta["processed_gmail_message_ids"].append(normalized["gmail_message_id"])

            meta["processed_gmail_message_ids"] = list(
                dict.fromkeys(meta["processed_gmail_message_ids"])
            )
            self.save_wardrobe(existing)
            self.save_meta(meta)
            return {
                "ok": True,
                "added": len(added),
                "updated": len(updated),
                "items": existing,
                "message_text": f"Wardrobe updated: {len(added)} added, {len(updated)} updated.",
            }

        def list_pending_items(self) -> dict[str, Any]:
            pending = [item for item in self.load_wardrobe() if item["status"] == "pending"]
            indexed = []
            for index, item in enumerate(pending, start=1):
                enriched = dict(item)
                enriched["pending_index"] = index
                indexed.append(enriched)
            if not indexed:
                message = "There are no pending wardrobe items waiting for confirmation."
            else:
                lines = ["Pending wardrobe items:"]
                for item in indexed:
                    lines.append(f"{item['pending_index']}. {_format_item_brief(item)}")
                message = "\n".join(lines)
            return {
                "ok": True,
                "items": indexed,
                "count": len(indexed),
                "message_text": message,
            }

        def confirm_pending_items(
            self,
            ids: list[str],
            edits: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            edit_map = {
                str(edit.get("id")): edit for edit in (edits or []) if isinstance(edit, dict) and edit.get("id")
            }
            wardrobe = self.load_wardrobe()
            confirmed: list[dict[str, Any]] = []
            for item in wardrobe:
                if item["id"] not in ids:
                    continue
                merged = {**item, **edit_map.get(item["id"], {}), "status": "confirmed"}
                updated = _normalize_item(merged, existing=item)
                item.update(updated)
                confirmed.append(item)
            self.save_wardrobe(wardrobe)
            if not confirmed:
                return {
                    "ok": False,
                    "confirmed": [],
                    "message_text": "No matching pending items were confirmed.",
                }
            return {
                "ok": True,
                "confirmed": confirmed,
                "message_text": f"Confirmed {len(confirmed)} wardrobe item(s).",
            }

        def get_forecast(self, location: str | None = None) -> dict[str, Any]:
            resolved_location = _normalize_text(location or self.load_profile().get("location"))
            if not resolved_location:
                return {
                    "ok": False,
                    "error": "missing_location",
                    "message_text": "A saved location is required before I can check the forecast.",
                }
            try:
                search_url = (
                    "https://geocoding-api.open-meteo.com/v1/search?"
                    + urllib.parse.urlencode({"name": resolved_location, "count": 1, "language": "en", "format": "json"})
                )
                search_data = _fetch_json(search_url)
                results = search_data.get("results") or []
                if not results:
                    raise ValueError("No geocoding results")
                place = results[0]
                latitude = float(place["latitude"])
                longitude = float(place["longitude"])
                timezone_name = str(place.get("timezone") or "auto")
                forecast_url = (
                    "https://api.open-meteo.com/v1/forecast?"
                    + urllib.parse.urlencode(
                        {
                            "latitude": latitude,
                            "longitude": longitude,
                            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                            "temperature_unit": "fahrenheit",
                            "forecast_days": 1,
                            "timezone": timezone_name,
                        }
                    )
                )
                forecast_data = _fetch_json(forecast_url)
                daily = forecast_data.get("daily") or {}
                payload = {
                    "ok": True,
                    "location": place.get("name") or resolved_location,
                    "date": (daily.get("time") or [_utc_now().date().isoformat()])[0],
                    "high_f": (daily.get("temperature_2m_max") or [72])[0],
                    "low_f": (daily.get("temperature_2m_min") or [60])[0],
                    "precipitation_probability": (daily.get("precipitation_probability_max") or [0])[0],
                    "weather_code": (daily.get("weather_code") or [2])[0],
                    "summary": _weather_summary((daily.get("weather_code") or [2])[0]),
                }
            except Exception as fetch_exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "error": "forecast_unavailable",
                    "location": resolved_location,
                    "details": str(fetch_exc),
                    "message_text": f"Weather lookup failed for {resolved_location}; using fallback outfit logic.",
                }
            normalized = _normalize_forecast(payload)
            normalized["message_text"] = (
                f"Forecast for {normalized['location']}: {normalized['high_f']}F / {normalized['low_f']}F, "
                f"{normalized['summary']}, {normalized['precipitation_probability']}% precipitation."
            )
            return normalized

        def generate_outfit_options(
            self,
            forecast: dict[str, Any] | None = None,
            limit: int = 3,
        ) -> dict[str, Any]:
            profile = self.load_profile()
            preferences = self.load_preferences()
            wardrobe = [item for item in self.load_wardrobe() if item["status"] == "confirmed"]
            weather = _normalize_forecast(forecast)
            if not profile.get("location"):
                return {
                    "ok": False,
                    "error": "missing_profile",
                    "message_text": "The user still needs onboarding before outfit recommendations can run.",
                    "options": [],
                    "weather": weather,
                }
            if not wardrobe:
                return {
                    "ok": False,
                    "error": "empty_wardrobe",
                    "message_text": "No confirmed wardrobe items are available yet.",
                    "options": [],
                    "weather": weather,
                }

            grouped: dict[str, list[dict[str, Any]]] = {category: [] for category in TOP_CATEGORIES}
            for item in wardrobe:
                grouped.setdefault(item["category"], []).append(item)

            ranked = {
                category: sorted(
                    items,
                    key=lambda item: self._score_item(item, weather, profile, preferences),
                    reverse=True,
                )[:4]
                for category, items in grouped.items()
            }

            combos: list[dict[str, Any]] = []
            outer_candidates = ranked["outerwear"][:2] if weather["needs_outerwear"] else [None]
            if not outer_candidates:
                outer_candidates = [None]

            if ranked["one_piece"]:
                shoe_pool = ranked["shoes"] or [None]
                for piece, shoes, outerwear in product(ranked["one_piece"][:4], shoe_pool, outer_candidates):
                    items = [piece]
                    missing = []
                    if shoes:
                        items.append(shoes)
                    else:
                        missing.append("shoes")
                    if outerwear:
                        items.append(outerwear)
                    elif weather["needs_outerwear"]:
                        missing.append("outerwear")
                    combos.append(self._build_option(items, missing, weather, profile, preferences, outfit_type="one_piece"))

            top_pool = ranked["top"] or [None]
            bottom_pool = ranked["bottom"] or [None]
            shoe_pool = ranked["shoes"] or [None]
            for top, bottom, shoes, outerwear in product(top_pool, bottom_pool, shoe_pool, outer_candidates):
                if not top and not bottom:
                    continue
                items = [item for item in [top, bottom, shoes, outerwear] if item]
                missing: list[str] = []
                if not top:
                    missing.append("top")
                if not bottom:
                    missing.append("bottom")
                if not shoes:
                    missing.append("shoes")
                if weather["needs_outerwear"] and not outerwear:
                    missing.append("outerwear")
                combos.append(self._build_option(items, missing, weather, profile, preferences, outfit_type="separates"))

            deduped: list[dict[str, Any]] = []
            seen = set()
            for option in sorted(combos, key=lambda row: row["score"], reverse=True):
                signature = (
                    tuple(sorted(item["id"] for item in option["items"])),
                    tuple(sorted(option["missing_categories"])),
                )
                if signature in seen:
                    continue
                seen.add(signature)
                deduped.append(option)
                if len(deduped) >= max(3, limit * 2):
                    break

            selected: list[dict[str, Any]] = []
            for option in deduped:
                overlap = False
                for chosen in selected:
                    chosen_ids = {item["id"] for item in chosen["items"]}
                    option_ids = {item["id"] for item in option["items"]}
                    if chosen_ids and option_ids and len(chosen_ids & option_ids) == len(option_ids):
                        overlap = True
                        break
                if overlap:
                    continue
                selected.append(option)
                if len(selected) >= limit:
                    break
            if len(selected) < limit:
                selected = deduped[:limit]

            for index, option in enumerate(selected, start=1):
                option["option_number"] = index

            pending_count = len([item for item in self.load_wardrobe() if item["status"] == "pending"])
            target_date = weather.get("date") or datetime.now().date().isoformat()
            message_lines = [
                f"Outfit shortlist for {target_date} in {weather['location'] or profile['location']}:",
                f"Weather: {weather['high_f']}F / {weather['low_f']}F, {weather['summary']}, {weather['precipitation_probability']}% precipitation.",
            ]
            for option in selected:
                message_lines.append(f"{option['option_number']}. {option['summary_text']}")
            if pending_count:
                message_lines.append(f"{pending_count} new clothing item(s) are pending confirmation and were not used yet.")
            if weather["used_fallback"]:
                message_lines.append("Weather lookup failed, so these picks use a generic weather fallback.")
            message_lines.append("Reply with 1, 2, or 3 to choose the outfit you want.")
            return {
                "ok": bool(selected),
                "weather": weather,
                "options": selected,
                "pending_item_count": pending_count,
                "message_text": "\n".join(message_lines),
            }

        def record_recommendation(self, event: dict[str, Any]) -> dict[str, Any]:
            options = event.get("options") or []
            if not options:
                return {
                    "ok": False,
                    "error": "missing_options",
                    "message_text": "Recommendations were not recorded because no outfit options were provided.",
                }
            rows = self.load_recommendations()
            recommendation = {
                "id": str(event.get("id") or f"rec-{uuid.uuid4().hex[:10]}"),
                "created_at": _iso_now(),
                "weather": event.get("weather") or {},
                "options": options,
                "selected_option": None,
                "message_text": event.get("message_text") or "",
            }
            rows.append(recommendation)
            self.save_recommendations(rows)
            meta = self.load_meta()
            meta["latest_recommendation_id"] = recommendation["id"]
            self.save_meta(meta)
            return {
                "ok": True,
                "recommendation": recommendation,
                "message_text": f"Saved recommendation {recommendation['id']} with {len(options)} option(s).",
            }

        def record_feedback(self, option_number: int | str, note: str | None = None) -> dict[str, Any]:
            try:
                selected_number = int(option_number)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "invalid_feedback",
                    "message_text": "Please reply with 1, 2, or 3 so I can learn which outfit you chose.",
                }
            rows = self.load_recommendations()
            meta = self.load_meta()
            latest_id = meta.get("latest_recommendation_id")
            target = None
            for row in reversed(rows):
                if row.get("id") == latest_id:
                    target = row
                    break
            if not target:
                return {
                    "ok": False,
                    "error": "missing_recommendation",
                    "message_text": "I do not have a recent outfit shortlist to attach that choice to.",
                }
            options = target.get("options") or []
            chosen = next((row for row in options if row.get("option_number") == selected_number), None)
            if not chosen:
                return {
                    "ok": False,
                    "error": "option_out_of_range",
                    "message_text": "That option number was not in the latest shortlist. Please reply with 1, 2, or 3.",
                }

            target["selected_option"] = selected_number
            target["feedback_note"] = _normalize_text(note)
            self.save_recommendations(rows)
            wardrobe = self.load_wardrobe()
            wardrobe_map = {item["id"]: item for item in wardrobe}
            for item in chosen.get("items", []):
                stored = wardrobe_map.get(item["id"])
                if not stored:
                    continue
                stored["last_worn_at"] = _iso_now()
                stored["times_worn"] = int(stored.get("times_worn") or 0) + 1
            self.save_wardrobe(wardrobe)

            preferences = self.load_preferences()
            for item in chosen.get("items", []):
                if item.get("color"):
                    preferences["colors"][item["color"]] = int(preferences["colors"].get(item["color"], 0)) + 1
                category = item.get("category") or ""
                if category:
                    preferences["categories"][category] = int(preferences["categories"].get(category, 0)) + 1
                for tag in item.get("style_tags", []):
                    preferences["style_tags"][tag] = int(preferences["style_tags"].get(tag, 0)) + 1
            self.save_preferences(preferences)
            return {
                "ok": True,
                "selected_option": selected_number,
                "option": chosen,
                "message_text": (
                    f"Locked in option {selected_number}: {chosen['summary_text']} "
                    "I updated your wear history and style preferences."
                ),
            }

        def extract_clothing_items(
            self,
            subject: str,
            body: str,
            sender: str = "",
            gmail_message_id: str = "",
        ) -> dict[str, Any]:
            combined = "\n".join([_normalize_text(subject), _normalize_text(body)])
            raw_chunks = re.split(r"[\n\r•]+|(?<=[.!?])\s+", combined)
            candidates: list[dict[str, Any]] = []
            seen_names = set()
            for chunk in raw_chunks:
                cleaned = _normalize_text(chunk)
                if not cleaned:
                    continue
                cleaned = re.sub(
                    r"^(your order|items in this order|order details|shipping update|delivered|shipped)\s*:\s*",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                )
                segments = [segment.strip(" -") for segment in re.split(r"\band\b|,", cleaned, flags=re.IGNORECASE)]
                for segment in segments:
                    normalized_segment = _normalize_text(segment).strip(".")
                    lowered = normalized_segment.lower()
                    if not normalized_segment:
                        continue
                    if not any(keyword in lowered for keywords in CATEGORY_KEYWORDS.values() for keyword in keywords):
                        continue
                    if any(token in lowered for token in ["shipping", "subtotal", "tracking", "return", "policy"]):
                        continue
                    if len(normalized_segment.split()) > 10:
                        continue
                    metadata = infer_metadata(normalized_segment)
                    candidate = _normalize_item(
                        {
                            "name": normalized_segment.title(),
                            "category": metadata["category"],
                            "source": "gmail",
                            "status": "pending",
                            "gmail_message_id": gmail_message_id,
                            "color": metadata["color"],
                            "material": metadata["material"],
                            "warmth": metadata["warmth"],
                            "formality": metadata["formality"],
                            "comfort_tags": metadata["comfort_tags"],
                            "style_tags": metadata["style_tags"],
                            "active_ok": metadata["active_ok"],
                        }
                    )
                    fingerprint = candidate["name"].lower()
                    if fingerprint in seen_names:
                        continue
                    seen_names.add(fingerprint)
                    candidates.append(candidate)
            if not candidates and any(
                keyword in combined.lower() for keywords in CATEGORY_KEYWORDS.values() for keyword in keywords
            ):
                fallback_name = _normalize_text(subject) or "New clothing item from email"
                candidates.append(
                    _normalize_item(
                        {
                            "name": fallback_name.title(),
                            "source": "gmail",
                            "status": "pending",
                            "gmail_message_id": gmail_message_id,
                        }
                    )
                )
            return {
                "ok": True,
                "items": candidates,
                "sender": sender,
                "gmail_message_id": gmail_message_id,
                "message_text": f"Extracted {len(candidates)} candidate clothing item(s) from the email.",
            }

        def _score_item(
            self,
            item: dict[str, Any],
            weather: dict[str, Any],
            profile: dict[str, Any],
            preferences: dict[str, Any],
        ) -> float:
            avg_temp = (weather["high_f"] + weather["low_f"]) / 2
            target_warmth = 1
            if avg_temp <= 45:
                target_warmth = 3
            elif avg_temp <= 60:
                target_warmth = 2
            elif avg_temp >= 78:
                target_warmth = 0

            score = 10.0 - abs(int(item.get("warmth", 1)) - target_warmth) * 2.0
            profile_styles = {value.lower() for value in profile.get("style_keywords", [])}
            comfort_preferences = {value.lower() for value in profile.get("comfort_preferences", [])}
            style_tags = {value.lower() for value in item.get("style_tags", [])}
            comfort_tags = {value.lower() for value in item.get("comfort_tags", [])}

            score += sum(2.0 for tag in style_tags if tag in profile_styles)
            score += sum(1.5 for tag in comfort_tags if tag in comfort_preferences)
            score += 0.8 * int(preferences["categories"].get(item["category"], 0))
            score += 0.4 * int(preferences["colors"].get(item.get("color", ""), 0))
            score += 0.8 * sum(int(preferences["style_tags"].get(tag, 0)) for tag in style_tags)

            if profile.get("lifestyle") == "active" and item.get("active_ok"):
                score += 1.5
            if profile.get("default_formality") == item.get("formality"):
                score += 1.0

            last_worn_at = _parse_dt(item.get("last_worn_at"))
            if last_worn_at:
                repeat_cutoff = _utc_now() - timedelta(days=int(profile.get("repeat_tolerance_days") or 3))
                if last_worn_at >= repeat_cutoff:
                    score -= 6.0
            if weather["conditions"] == "rainy" and item["category"] == "shoes":
                lowered = item["name"].lower()
                if "sandals" in lowered or "heels" in lowered:
                    score -= 3.0
                if "boots" in lowered:
                    score += 2.0
            return score

        def _build_option(
            self,
            items: list[dict[str, Any]],
            missing_categories: list[str],
            weather: dict[str, Any],
            profile: dict[str, Any],
            preferences: dict[str, Any],
            outfit_type: str,
        ) -> dict[str, Any]:
            clean_missing = sorted(dict.fromkeys(missing_categories))
            score = sum(self._score_item(item, weather, profile, preferences) for item in items)
            score -= 5.0 * len(clean_missing)
            formality_values = [item.get("formality") for item in items]
            if len(set(formality_values)) > 2:
                score -= 1.5
            color_values = [item.get("color") for item in items if item.get("color")]
            if len(color_values) >= 2 and len(set(color_values)) == 1:
                score -= 0.5
            item_names = ", ".join(item["name"] for item in items) if items else "No confirmed items yet"
            vibe = ", ".join(sorted({tag for item in items for tag in item.get("style_tags", [])})) or profile.get(
                "default_formality",
                "casual",
            )
            gap_note = ""
            if clean_missing:
                gap_note = f" Needs: {', '.join(clean_missing)}."
            summary = (
                f"{item_names}. Built for {weather['summary']} and a {vibe} vibe."
                f"{gap_note}"
            )
            return {
                "score": round(score, 2),
                "items": items,
                "missing_categories": clean_missing,
                "outfit_type": outfit_type,
                "summary_text": summary,
                "style_tags": sorted({tag for item in items for tag in item.get("style_tags", [])}),
            }

store = StateStore()

# BEGIN_EMBEDDED_DEMO_PREFS
EMBEDDED_DEMO_PREFS = json.loads(
    r"""{}"""
)
# END_EMBEDDED_DEMO_PREFS

# BEGIN_EMBEDDED_DEMO_CONFIG
EMBEDDED_DEMO_CONFIG = json.loads(
    r"""{"target_date": "", "label": "Wardrobe demo run"}"""
)
# END_EMBEDDED_DEMO_CONFIG


def _split_pref_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\n/]+", value) if part.strip()]
    return [str(value).strip()]


def _parse_repeat_tolerance_days(value: Any) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return 3
    if any(token in text for token in ["week", "weekly"]):
        return 7
    if "day" in text:
        match = re.search(r"(\d+)", text)
        if match:
            return max(1, int(match.group(1)))
        if "every day" in text or "daily" in text:
            return 1
    if "twice" in text and "week" in text:
        return 3
    match = re.search(r"(\d+)", text)
    if match:
        return max(1, int(match.group(1)))
    return 3


def _infer_default_formality(style_values: list[str], lifestyle: str) -> str:
    lowered = {value.lower() for value in style_values}
    if "formal" in lowered:
        return "formal"
    if "smart-casual" in lowered or "smart casual" in lowered:
        return "smart-casual"
    if lifestyle.lower() == "professional":
        return "smart-casual"
    return "casual"


def _normalize_demo_preferences(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    style_values = _split_pref_values(raw.get("style_keywords") or raw.get("style"))
    comfort_values = _split_pref_values(raw.get("comfort_preferences"))
    lifestyle = str(raw.get("lifestyle") or "professional").strip().lower() or "professional"
    profile = {
        "location": str(raw.get("location") or "").strip(),
        "style_keywords": style_values,
        "comfort_preferences": comfort_values,
        "lifestyle": lifestyle,
        "repeat_tolerance_days": _parse_repeat_tolerance_days(
            raw.get("repeat_tolerance_days") or raw.get("repeat_tolerance")
        ),
        "default_formality": _infer_default_formality(style_values, lifestyle),
    }
    wardrobe_items = raw.get("wardrobe_items") or []
    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(wardrobe_items):
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized.setdefault("id", f"demo-item-{index + 1}")
        normalized.setdefault("source", "manual")
        normalized.setdefault("status", "confirmed")
        normalized.setdefault("times_worn", 0)
        normalized.setdefault("last_worn_at", None)
        normalized_items.append(normalized)
    return profile, normalized_items


def _reset_store_from_embedded_demo() -> dict[str, Any]:
    if not EMBEDDED_DEMO_PREFS:
        return {
            "ok": False,
            "seeded": False,
            "message_text": "No embedded demo onboarding data is available yet.",
        }
    profile, wardrobe_items = _normalize_demo_preferences(EMBEDDED_DEMO_PREFS)
    store.save_profile(profile)
    store.save_wardrobe([])
    store.save_preferences({"colors": {}, "categories": {}, "style_tags": {}})
    store.save_recommendations([])
    store.save_meta({"latest_recommendation_id": None, "processed_gmail_message_ids": []})
    store.upsert_wardrobe_items(wardrobe_items)
    snapshot = store.get_state_snapshot()
    return {
        "ok": True,
        "seeded": True,
        "state_snapshot": snapshot,
        "message_text": (
            f"Seeded demo onboarding for {profile['location']} with "
            f"{snapshot['counts']['confirmed']} confirmed wardrobe items."
        ),
    }


def _resolve_demo_target_date() -> str:
    configured = str(EMBEDDED_DEMO_CONFIG.get("target_date") or "").strip()
    if configured:
        try:
            return datetime.fromisoformat(configured).date().isoformat()
        except ValueError:
            pass
    return (datetime.now().date() + timedelta(days=1)).isoformat()


def _fetch_json_payload(url: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ara-hackathon-demo-ui/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    payload = json.loads(raw) if raw else {}
    return payload if isinstance(payload, dict) else {}


def _fetch_target_date_forecast(location: str, target_date: str) -> dict[str, Any]:
    if not location:
        return {
            "ok": False,
            "error": "missing_location",
            "location": "",
            "message_text": "A location is required before the forecast can be fetched.",
        }
    try:
        search_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode({"name": location, "count": 1, "language": "en", "format": "json"})
        )
        search_data = _fetch_json_payload(search_url)
        results = search_data.get("results") or []
        if not results:
            raise ValueError("No geocoding results")
        place = results[0]
        target = datetime.fromisoformat(target_date).date()
        today = datetime.now().date()
        delta_days = max(0, (target - today).days)
        forecast_days = max(1, min(16, delta_days + 1))
        forecast_url = (
            "https://api.open-meteo.com/v1/forecast?"
            + urllib.parse.urlencode(
                {
                    "latitude": float(place["latitude"]),
                    "longitude": float(place["longitude"]),
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "temperature_unit": "fahrenheit",
                    "forecast_days": forecast_days,
                    "timezone": str(place.get("timezone") or "auto"),
                }
            )
        )
        forecast_data = _fetch_json_payload(forecast_url)
        daily = forecast_data.get("daily") or {}
        dates = daily.get("time") or []
        if target.isoformat() not in dates:
            raise ValueError(f"No forecast available for {target.isoformat()}")
        index = dates.index(target.isoformat())
        return {
            "ok": True,
            "location": place.get("name") or location,
            "date": target.isoformat(),
            "high_f": (daily.get("temperature_2m_max") or [72])[index],
            "low_f": (daily.get("temperature_2m_min") or [60])[index],
            "precipitation_probability": (daily.get("precipitation_probability_max") or [0])[index],
            "weather_code": (daily.get("weather_code") or [2])[index],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "forecast_unavailable",
            "location": location,
            "date": target_date,
            "details": str(exc),
            "message_text": f"Weather lookup failed for {location}; using fallback outfit logic.",
        }

SYSTEM_INSTRUCTIONS = """
You are a wardrobe assistant for a single user.

Primary jobs:
1. Onboard the user by collecting location, style keywords, comfort preferences, lifestyle, repeat tolerance, and 10-15 wardrobe items.
2. On scheduled runs, scan Gmail for recent clothing-order emails, extract candidate wardrobe items as pending, check the forecast, generate the top 3 outfit options, record them, and send the shortlist through linq_send_message if available.
3. When the user replies with exactly 1, 2, or 3, treat that as feedback on the latest outfit shortlist and call record_feedback.

Workflow rules:
- Always start by calling get_state_snapshot.
- If there is no live user message to respond to, treat the run as autonomous task mode.
- In autonomous task mode, call run_demo_daily_brief once. If it returns ok=false, return its message_text exactly and stop. If it returns ok=true, send its message_text through linq_send_message if available, then reply with a short confirmation that the outfit text was sent. Do not ask onboarding questions in autonomous task mode.
- If onboarding is incomplete in an interactive chat, ask short, guided questions and save what you learn with save_profile and upsert_wardrobe_items.
- Only store manual onboarding items as source=manual and status=confirmed.
- During the morning workflow, call gmail_search_emails for recent apparel order emails. For each unseen candidate email, use extract_clothing_items, then call upsert_wardrobe_items with those items. Email-derived items must remain pending until the user confirms them.
- Call list_pending_items when you need to review pending items with the user.
- Only confirmed items can be used in recommendations.
- For recommendations, call get_forecast, then generate_outfit_options, then record_recommendation.
- Send the exact shortlist text from generate_outfit_options.message_text through linq_send_message when possible. If messaging is unavailable, send the same content in chat.
- If the user wants to confirm pending items, call confirm_pending_items with the chosen ids and any edits they supplied.
- Never invent owned items. If the wardrobe has a gap, mention the missing category clearly.
- Keep responses concise, practical, and upbeat.







""".strip()


@ara.tool
def get_state_snapshot() -> dict[str, Any]:
    return store.get_state_snapshot()


@ara.tool
def autonomous_run_guard() -> dict[str, Any]:
    snapshot = store.get_state_snapshot()
    if snapshot.get("onboarding_complete"):
        return {
            "ok": True,
            "ready": True,
            "state_snapshot": snapshot,
            "message_text": "Autonomous run can proceed.",
        }
    return {
        "ok": True,
        "ready": False,
        "error": "onboarding_incomplete",
        "state_snapshot": snapshot,
        "message_text": (
            "Onboarding is incomplete. Open this agent in Ara chat at app.ara.so, "
            "finish the setup conversation there, then run `ara run app.py` again or wait for cron."
        ),
    }


@ara.tool
def run_demo_daily_brief() -> dict[str, Any]:
    snapshot = store.get_state_snapshot()
    seeded = False
    if not snapshot.get("onboarding_complete") and EMBEDDED_DEMO_PREFS:
        seed_result = _reset_store_from_embedded_demo()
        if not seed_result.get("ok"):
            return seed_result
        seeded = bool(seed_result.get("seeded"))
        snapshot = seed_result.get("state_snapshot") or store.get_state_snapshot()
    if not snapshot.get("onboarding_complete"):
        return {
            "ok": False,
            "error": "onboarding_incomplete",
            "message_text": (
                "Onboarding is incomplete. Open this agent in Ara chat at app.ara.so, "
                "finish the setup conversation there, then run `ara run app.py` again or wait for cron."
            ),
        }
    target_date = _resolve_demo_target_date()
    forecast = _fetch_target_date_forecast(snapshot["profile"]["location"], target_date)
    options = store.generate_outfit_options(forecast=forecast, limit=3)
    if not options.get("ok"):
        return options
    saved = store.record_recommendation(options)
    return {
        "ok": True,
        "seeded": seeded,
        "target_date": target_date,
        "weather": options.get("weather"),
        "options": options.get("options", []),
        "message_text": options.get("message_text"),
        "recorded": saved.get("ok", False),
    }


@ara.tool
def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return store.save_profile(profile)


@ara.tool
def upsert_wardrobe_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    return store.upsert_wardrobe_items(items)


@ara.tool
def list_pending_items() -> dict[str, Any]:
    return store.list_pending_items()


@ara.tool
def confirm_pending_items(ids: list[str], edits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return store.confirm_pending_items(ids=ids, edits=edits)


@ara.tool
def extract_clothing_items(
    subject: str,
    body: str,
    sender: str = "",
    gmail_message_id: str = "",
) -> dict[str, Any]:
    return store.extract_clothing_items(
        subject=subject,
        body=body,
        sender=sender,
        gmail_message_id=gmail_message_id,
    )


@ara.tool
def get_forecast(location: str | None = None) -> dict[str, Any]:
    return store.get_forecast(location)


@ara.tool
def generate_outfit_options(forecast: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
    return store.generate_outfit_options(forecast=forecast, limit=limit)


@ara.tool
def record_recommendation(event: dict[str, Any]) -> dict[str, Any]:
    return store.record_recommendation(event)


@ara.tool
def record_feedback(option_number: int | str, note: str | None = None) -> dict[str, Any]:
    return store.record_feedback(option_number=option_number, note=note)


app = ara.Automation(
    "outfit-recommender-jhu-hackathon",
    system_instructions=SYSTEM_INSTRUCTIONS,
    tools=[
        get_state_snapshot,
        autonomous_run_guard,
        run_demo_daily_brief,
        save_profile,
        upsert_wardrobe_items,
        list_pending_items,
        confirm_pending_items,
        extract_clothing_items,
        get_forecast,
        generate_outfit_options,
        record_recommendation,
        record_feedback,
    ],
)
