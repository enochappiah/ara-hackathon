# Outfit Recommender MVP

Single-user Ara wardrobe assistant for the Ara x JHU hackathon.

## What It Does

- Guides the user through onboarding for location, style, comfort preferences, lifestyle, and a starter wardrobe.
- Scans Gmail for clothing-order emails and stores extracted items as `pending`.
- Fetches weather for the saved location with Open-Meteo.
- Builds a top-3 outfit shortlist from confirmed wardrobe items only.
- Sends the daily shortlist through Ara messaging tools like iMessage when available.
- Learns from replies `1`, `2`, or `3` by updating wear history and lightweight preference counters.

## Project Layout

- `app.py`: Ara automation entrypoint and tool registration.
- `demo_ui.py`: local Flask demo UI that prefills onboarding, previews outfits, and triggers Ara deploy/run.
- `outfit_engine.py`: deterministic state, ingestion, weather, recommendation, and feedback logic.
- `tests/test_outfit_engine.py`: local unit tests for the core flows.
- `state/`: runtime state files created by the agent.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

## Run The Demo UI

```bash
python3 demo_ui.py
```

Then open `http://127.0.0.1:5050`.

What the UI does on submit:

- Prefills from `user_preferences.json`
- Saves the edited onboarding data back to `user_preferences.json`
- Seeds the local state files for preview
- Updates the embedded demo payload in `app.py`
- Runs `ara deploy app.py`
- Runs `ara run app.py`

The default send date is tomorrow, so on April 19, 2026 the UI will target April 20, 2026.

## Deploy To Ara

```bash
ara auth login
ara deploy app.py
ara run app.py
```

In `app.ara.so`:

- Enable Gmail access.
- Enable your phone route so `linq_send_message` can reach iMessage/SMS/RCS.
- Configure the daily cron in the UI.

## Suggested Demo Flow

1. Open the agent and complete onboarding with a small capsule wardrobe.
2. Run the automation once to ingest recent clothing-order emails.
3. Review pending items and confirm any good matches.
4. Run the morning flow so the agent checks weather and sends the top 3 outfit options.
5. Reply `1`, `2`, or `3` and show the updated preference learning behavior.

## State Files

- `state/profile.json`
- `state/wardrobe.json`
- `state/preferences.json`
- `state/recommendations.jsonl`
- `state/meta.json`
