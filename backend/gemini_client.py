"""Minimal Gemini REST client for reading the weekly meal-plan photo into a
day-by-day table. Uses plain httpx instead of the google-generativeai SDK to
avoid an extra dependency for what's a single API call."""
from backend.config import settings
from datetime import date
import httpx, json, logging

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """This image is a school cafeteria's weekly lunch menu ("Speiseplan"), in German.

Extract ONLY the LUNCH ("Mittagessen") items for each day. If the image also
shows breakfast or dinner ("Abendessen") sections, ignore those entirely.

For each day shown, read its actual calendar date directly from the image
(the columns are usually headed with a weekday and a date like
"Montag, 20.07.2026"). If a year is missing, infer it from context (e.g. a
"Speiseplan vom 13.07. bis 19.07." header) or assume the current year if
nothing else indicates otherwise. Today's date is {today} — use that only as
a fallback reference for inferring an ambiguous year, not as the date of any
specific day.

If a day offers multiple menu options (e.g. "Menü 1"/"Menü 2", or dietary
variants like vegetarian/diabetic/halal), combine them into one short, readable
description separated by " / ". Keep soup and dessert out of the description
unless there is nothing else for that day. Skip weekend columns only if they
carry no menu at all (e.g. just "WE MENÜ" placeholders with no actual dish).

Respond with ONLY a JSON array (no markdown fences, no commentary), like:
[{{"date": "2026-07-20", "meal": "Cordon bleu vom Schwein, Pommes frites, Salatteller"}}, ...]
"""

class GeminiError(Exception):
    pass

async def extract_meal_days(image_bytes: bytes, mime_type: str) -> list[dict]:
    if not settings.gemini_api_key:
        raise GeminiError("Gemini ist nicht konfiguriert (GEMINI_API_KEY fehlt)")

    import base64
    body = {
        "contents": [{
            "parts": [
                {"text": PROMPT.format(today=date.today().isoformat())},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
            ]
        }],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    url = GEMINI_URL.format(model=settings.gemini_model)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, params={"key": settings.gemini_api_key}, json=body)

    if resp.status_code != 200:
        logger.warning("Gemini request failed: %s %s", resp.status_code, resp.text[:500])
        raise GeminiError(f"Gemini-Anfrage fehlgeschlagen ({resp.status_code})")

    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.warning("Unexpected Gemini response shape: %s", str(data)[:500])
        raise GeminiError("Gemini hat keine verwertbare Antwort geliefert")

    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Could not parse Gemini JSON: %s", text[:500])
        raise GeminiError("Konnte die Antwort von Gemini nicht lesen")

    days = []
    for item in parsed if isinstance(parsed, list) else []:
        d, m = item.get("date"), item.get("meal")
        if d and m:
            days.append({"date": d, "meal": m})
    if not days:
        raise GeminiError("Es konnten keine Tage aus dem Bild erkannt werden")
    return days
