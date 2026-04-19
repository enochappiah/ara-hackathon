from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request

from outfit_engine import StateStore

PROJECT_ROOT = Path(__file__).resolve().parent
USER_PREFERENCES_PATH = PROJECT_ROOT / "user_preferences.json"
APP_ENTRY_PATH = PROJECT_ROOT / "app.py"
LOCAL_STATE_PATH = PROJECT_ROOT / "state"

DEFAULT_USER_PREFERENCES = {
    "location": "",
    "style": "",
    "comfort_preferences": "",
    "lifestyle": "professional",
    "repeat_tolerance": "once a week",
    "wardrobe_items": [],
}

app = Flask(__name__, template_folder="templates", static_folder="static")


def load_user_preferences() -> dict[str, Any]:
    if not USER_PREFERENCES_PATH.exists():
        return dict(DEFAULT_USER_PREFERENCES)
    try:
        payload = json.loads(USER_PREFERENCES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(DEFAULT_USER_PREFERENCES)
    if not isinstance(payload, dict):
        return dict(DEFAULT_USER_PREFERENCES)
    merged = {**DEFAULT_USER_PREFERENCES, **payload}
    if not isinstance(merged.get("wardrobe_items"), list):
        merged["wardrobe_items"] = []
    return merged


def save_user_preferences(preferences: dict[str, Any]) -> None:
    USER_PREFERENCES_PATH.write_text(
        json.dumps(preferences, indent=2, sort_keys=False),
        encoding="utf-8",
    )


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
    match = re.search(r"(\d+)", text)
    if match:
        return max(1, int(match.group(1)))
    return 3


def _infer_default_formality(style_text: str, lifestyle: str) -> str:
    lowered = {value.lower() for value in _split_pref_values(style_text)}
    if "formal" in lowered:
        return "formal"
    if "smart-casual" in lowered or "smart casual" in lowered:
        return "smart-casual"
    if lifestyle.lower() == "professional":
        return "smart-casual"
    return "casual"


def preferences_to_profile(preferences: dict[str, Any]) -> dict[str, Any]:
    lifestyle = str(preferences.get("lifestyle") or "professional").strip().lower() or "professional"
    style_text = str(preferences.get("style") or "").strip()
    return {
        "location": str(preferences.get("location") or "").strip(),
        "style_keywords": _split_pref_values(style_text),
        "comfort_preferences": _split_pref_values(preferences.get("comfort_preferences")),
        "lifestyle": lifestyle,
        "repeat_tolerance_days": _parse_repeat_tolerance_days(preferences.get("repeat_tolerance")),
        "default_formality": _infer_default_formality(style_text, lifestyle),
    }


def normalize_wardrobe_items(preferences: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(preferences.get("wardrobe_items") or []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "id": str(raw.get("id") or f"ui-item-{index + 1}"),
                "name": name,
                "category": str(raw.get("category") or "top").strip().lower(),
                "color": str(raw.get("color") or "").strip().lower(),
                "material": str(raw.get("material") or "").strip().lower(),
                "warmth": int(raw.get("warmth") if str(raw.get("warmth", "")).strip() else 1),
                "formality": str(raw.get("formality") or "casual").strip().lower(),
                "comfort_tags": _split_pref_values(raw.get("comfort_tags")),
                "active_ok": bool(raw.get("active_ok")),
                "source": str(raw.get("source") or "manual").strip().lower(),
                "status": str(raw.get("status") or "confirmed").strip().lower(),
                "last_worn_at": raw.get("last_worn_at"),
                "times_worn": int(raw.get("times_worn") or 0),
            }
        )
    return normalized


def seed_local_state(preferences: dict[str, Any], state_path: Path = LOCAL_STATE_PATH) -> dict[str, Any]:
    store = StateStore(state_path)
    store.save_profile(preferences_to_profile(preferences))
    store.save_wardrobe([])
    store.save_preferences({"colors": {}, "categories": {}, "style_tags": {}})
    store.save_recommendations([])
    store.save_meta({"latest_recommendation_id": None, "processed_gmail_message_ids": []})
    store.upsert_wardrobe_items(normalize_wardrobe_items(preferences))
    return store.get_state_snapshot()


def fetch_target_date_forecast(location: str, target_date: str) -> dict[str, Any]:
    if not location:
        return {
            "ok": False,
            "location": "",
            "date": target_date,
            "message_text": "Missing location; using fallback recommendation logic.",
        }
    try:
        geocode_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode({"name": location, "count": 1, "language": "en", "format": "json"})
        )
        request = urllib.request.Request(
            geocode_url,
            headers={"Accept": "application/json", "User-Agent": "ara-hackathon-demo-ui/1.0"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            geocode_payload = json.loads(response.read().decode("utf-8", errors="replace"))
        results = geocode_payload.get("results") or []
        if not results:
            raise ValueError("No geocoding results")
        place = results[0]
        target = datetime.fromisoformat(target_date).date()
        delta_days = max(0, (target - date.today()).days)
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
        forecast_request = urllib.request.Request(
            forecast_url,
            headers={"Accept": "application/json", "User-Agent": "ara-hackathon-demo-ui/1.0"},
        )
        with urllib.request.urlopen(forecast_request, timeout=20) as response:
            forecast_payload = json.loads(response.read().decode("utf-8", errors="replace"))
        daily = forecast_payload.get("daily") or {}
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
            "location": location,
            "date": target_date,
            "details": str(exc),
            "message_text": f"Weather lookup failed for {location}; using fallback recommendation logic.",
        }


def build_local_preview(preferences: dict[str, Any], target_date: str) -> dict[str, Any]:
    snapshot = seed_local_state(preferences)
    store = StateStore(LOCAL_STATE_PATH)
    forecast = fetch_target_date_forecast(snapshot["profile"]["location"], target_date)
    preview = store.generate_outfit_options(forecast=forecast, limit=3)
    preview["target_date"] = target_date
    return preview


def embed_demo_payloads(
    preferences: dict[str, Any],
    target_date: str,
    app_path: Path = APP_ENTRY_PATH,
) -> None:
    source = app_path.read_text(encoding="utf-8")
    pref_json = json.dumps(preferences, indent=2)
    config_json = json.dumps({"target_date": target_date, "label": "Demo UI submission"}, indent=2)

    pref_pattern = re.compile(
        r"# BEGIN_EMBEDDED_DEMO_PREFS\nEMBEDDED_DEMO_PREFS = json.loads\(\n    r\"\"\".*?\"\"\"\n\)\n# END_EMBEDDED_DEMO_PREFS",
        re.S,
    )
    config_pattern = re.compile(
        r"# BEGIN_EMBEDDED_DEMO_CONFIG\nEMBEDDED_DEMO_CONFIG = json.loads\(\n    r\"\"\".*?\"\"\"\n\)\n# END_EMBEDDED_DEMO_CONFIG",
        re.S,
    )

    pref_block = (
        "# BEGIN_EMBEDDED_DEMO_PREFS\n"
        "EMBEDDED_DEMO_PREFS = json.loads(\n"
        f'    r"""{pref_json}"""\n'
        ")\n"
        "# END_EMBEDDED_DEMO_PREFS"
    )
    config_block = (
        "# BEGIN_EMBEDDED_DEMO_CONFIG\n"
        "EMBEDDED_DEMO_CONFIG = json.loads(\n"
        f'    r"""{config_json}"""\n'
        ")\n"
        "# END_EMBEDDED_DEMO_CONFIG"
    )

    source, pref_count = pref_pattern.subn(pref_block, source, count=1)
    source, config_count = config_pattern.subn(config_block, source, count=1)
    if pref_count != 1 or config_count != 1:
        raise ValueError("Could not locate embedded demo payload markers in app.py")
    app_path.write_text(source, encoding="utf-8")


def run_ara_command(args: list[str], timeout_seconds: int = 240) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "output": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "output": f"Command timed out: {' '.join(args)}\n{exc}",
        }

    output_parts = [part for part in [completed.stdout, completed.stderr] if part]
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": "\n".join(output_parts).strip(),
    }


def build_template_context(
    *,
    preferences: dict[str, Any] | None = None,
    target_date: str | None = None,
    preview: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    deploy_result: dict[str, Any] | None = None,
    run_result: dict[str, Any] | None = None,
    error_message: str = "",
) -> dict[str, Any]:
    current_preferences = preferences or load_user_preferences()
    effective_target_date = target_date or (date.today() + timedelta(days=1)).isoformat()
    current_snapshot = snapshot or StateStore(LOCAL_STATE_PATH).get_state_snapshot()
    return {
        "preferences": current_preferences,
        "target_date": effective_target_date,
        "preview": preview,
        "snapshot": current_snapshot,
        "deploy_result": deploy_result,
        "run_result": run_result,
        "error_message": error_message,
    }


def parse_submitted_preferences(form: Any) -> tuple[dict[str, Any], str]:
    wardrobe_raw = form.get("wardrobe_payload", "[]")
    try:
        wardrobe_items = json.loads(wardrobe_raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Wardrobe payload is not valid JSON: {exc}") from exc
    if not isinstance(wardrobe_items, list):
        raise ValueError("Wardrobe payload must be a JSON array.")
    target_date = str(form.get("target_date") or (date.today() + timedelta(days=1)).isoformat()).strip()
    preferences = {
        "location": str(form.get("location") or "").strip(),
        "style": str(form.get("style") or "").strip(),
        "comfort_preferences": str(form.get("comfort_preferences") or "").strip(),
        "lifestyle": str(form.get("lifestyle") or "professional").strip(),
        "repeat_tolerance": str(form.get("repeat_tolerance") or "once a week").strip(),
        "wardrobe_items": wardrobe_items,
    }
    return preferences, target_date


@app.get("/")
def index() -> str:
    return render_template("demo.html", **build_template_context())


@app.post("/submit")
def submit() -> str:
    try:
        preferences, target_date = parse_submitted_preferences(request.form)
        save_user_preferences(preferences)
        snapshot = seed_local_state(preferences)
        preview = build_local_preview(preferences, target_date)
        embed_demo_payloads(preferences, target_date)
        deploy_result = run_ara_command(["ara", "deploy", "app.py"])
        run_result = None
        if deploy_result["ok"]:
            run_result = run_ara_command(["ara", "run", "app.py"])
        return render_template(
            "demo.html",
            **build_template_context(
                preferences=preferences,
                target_date=target_date,
                preview=preview,
                snapshot=snapshot,
                deploy_result=deploy_result,
                run_result=run_result,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return render_template(
            "demo.html",
            **build_template_context(error_message=str(exc)),
        )


if __name__ == "__main__":
    app.run(debug=True, port=5050)
