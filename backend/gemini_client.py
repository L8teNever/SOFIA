"""Minimal Gemini REST client for reading the weekly meal-plan photo into a
day-by-day table. Uses plain httpx instead of the google-generativeai SDK to
avoid an extra dependency for what's a single API call."""
from backend.config import settings
from datetime import date
from PIL import Image, ImageOps
import httpx, json, logging, base64, io, re

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """This image is a school cafeteria's weekly lunch menu ("Speiseplan"), in German.

Extract the lunch items ("Mittagessen") for each day shown. If the image also
shows breakfast or dinner ("Abendessen") sections, ignore those entirely.

For each day shown, read its actual calendar date directly from the image
(the columns are usually headed with a weekday and a date like
"Montag, 20.07.2026"). If a year is missing, infer it from context (e.g. a
"Speiseplan vom 13.07. bis 19.07." header) or assume the current year if
nothing else indicates otherwise. Today's date is {today} — use that only as
a fallback reference for inferring an ambiguous year, not as the date of any
specific day. Ignore weekend columns (Samstag / Sonntag, Saturday / Sunday) completely — only extract Monday through Friday ("Montag" bis "Freitag").

For each day, carefully identify the dishes and categorize them into:
- soup: Suppe / Tagessuppe / Vorspeise (if available)
- main: Hauptgericht / Menü 1 / Vollkost / Normal
- vegetarian: Vegetarisches Gericht / Menü 2 / Veggie (if available)
- muslim: Muslimisch / Schweinefleischfrei / Geflügel / Rind / Halal (if specifically indicated or offered as an alternative)
- dessert: Dessert / Nachspeise / Obst / Pudding (if available)

If multiple components belong to one meal (e.g. main dish + side dishes), combine them into a clear description (e.g. "Cordon bleu vom Schwein mit Pommes frites und Salatteller"). If a category is not present on that day, omit it or set it to null.

Respond with ONLY a JSON array (no markdown fences, no commentary), like:
[
  {{
    "date": "2026-07-20",
    "soup": "Grießnockerlsuppe",
    "main": "Cordon bleu vom Schwein mit Pommes frites",
    "vegetarian": "Gemüselasagne mit Beilagensalat",
    "muslim": "Puten-Cordon-bleu mit Pommes frites",
    "dessert": "Obstsalat"
  }},
  ...
]
"""

TIMETABLE_PROMPT = """This image shows a German school weekly timetable ("Stundenplan"), typically a
grid with weekdays (Montag-Freitag) as columns and lesson periods as rows, each
row labelled with a period number and/or a time range (e.g. "1. 08:00-08:45").

For EVERY filled cell in the grid (ignore empty/free cells), extract one entry with:
- weekday: the German weekday name for that column — "Montag", "Dienstag", "Mittwoch", "Donnerstag", or "Freitag". Ignore Saturday/Sunday columns entirely if present.
- start_time / end_time: the lesson's start and end time as "HH:MM" (24h). Read this from the row's time label if shown; otherwise infer from a visible period schedule (periods are usually 45 or 50 minutes with short breaks between).
- subject: the full German subject name, expanded from any abbreviation shown using standard school abbreviations (e.g. "M"/"Ma" -> "Mathematik", "D" -> "Deutsch", "E"/"En" -> "Englisch", "Sp" -> "Sport", "Bio" -> "Biologie", "Ph"/"Phy" -> "Physik", "Ch" -> "Chemie", "Geo"/"Erdk" -> "Erdkunde", "Ge"/"Gesch" -> "Geschichte", "Ku" -> "Kunst", "Mu" -> "Musik", "Reli"/"Re" -> "Religion", "Eth" -> "Ethik", "Inf" -> "Informatik", "WiPo"/"Pol" -> "Politik", "Fr"/"Frz" -> "Französisch", "La" -> "Latein"). If you cannot confidently expand it, just use the abbreviation as shown.
- subject_short: the short form/abbreviation exactly as printed on the timetable, if visible (otherwise omit).
- room: the room number/name if shown in the cell, otherwise omit.

If one subject visibly spans two consecutive periods (a double lesson / "Doppelstunde" shown as one merged cell), output it as TWO separate entries, one per period's time range, both with the same subject.

Respond with ONLY a JSON array (no markdown fences, no commentary), like:
[
  {"weekday": "Montag", "start_time": "08:00", "end_time": "08:45", "subject": "Mathematik", "subject_short": "M", "room": "204"},
  ...
]
"""

_WEEKDAY_MAP = {
    "montag": 0, "mo": 0,
    "dienstag": 1, "di": 1,
    "mittwoch": 2, "mi": 2,
    "donnerstag": 3, "do": 3,
    "freitag": 4, "fr": 4,
}

def _parse_hhmm(raw) -> int | None:
    s = str(raw or "").strip()
    m = re.match(r'^(\d{1,2}):?(\d{2})$', s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h * 100 + mi

class GeminiError(Exception):
    pass

def _optimize_image(image_bytes: bytes) -> tuple[bytes, str]:
    """Ensures image is in a supported format (JPEG), auto-rotated according to EXIF,
    and downscaled if oversized to stay within payload limits and ensure fast OCR."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_dim = 2048
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning("Konnte Bild nicht via Pillow vorverarbeiten: %s", e)
        return image_bytes, "image/jpeg"

async def _call_gemini_json(prompt: str, image_bytes: bytes = None, mime_type: str = None):
    """Shared Gemini call used by every AI feature: sends a prompt (plus an
    optional image for the vision-based features) and returns the parsed JSON
    response body. Raises GeminiError on any failure (missing key, blocked
    prompt, bad response shape, unparsable JSON) so callers only need to
    handle their own field-level validation."""
    if not settings.gemini_api_key:
        logger.error("Gemini API key is missing. Set GEMINI_API_KEY in your .env and recreate container.")
        raise GeminiError("Gemini ist nicht konfiguriert (GEMINI_API_KEY fehlt in Umgebungsvariablen)")

    parts = [{"text": prompt}]
    if image_bytes is not None:
        # Optimize and normalize image (JPEG format, proper rotation, reasonable size)
        optimized_bytes, target_mime = _optimize_image(image_bytes)
        parts.append({"inline_data": {"mime_type": target_mime, "data": base64.b64encode(optimized_bytes).decode()}})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    model = settings.gemini_model
    # gemini-2.0-flash is retired by Google; auto-upgrade to gemini-3.6-flash
    if model in ("gemini-2.0-flash", "gemini-2.0-flash-exp"):
        model = "gemini-3.6-flash"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            GEMINI_URL.format(model=model),
            params={"key": settings.gemini_api_key},
            json=body,
        )

        # Fallback to gemini-3.6-flash if custom/deprecated model returned 404
        if resp.status_code == 404 and model != "gemini-3.6-flash":
            logger.info("Modell %s meldete 404; Fallback auf gemini-3.6-flash...", model)
            model = "gemini-3.6-flash"
            resp = await client.post(
                GEMINI_URL.format(model=model),
                params={"key": settings.gemini_api_key},
                json=body,
            )

    if resp.status_code != 200:
        err_msg = ""
        try:
            err_json = resp.json()
            err_msg = err_json.get("error", {}).get("message", "")
        except Exception:
            err_msg = resp.text[:200]
        logger.warning("Gemini request failed (%s): %s", resp.status_code, err_msg or resp.text[:500])
        detail = f"Gemini-Anfrage fehlgeschlagen ({resp.status_code})"
        if err_msg:
            detail += f": {err_msg}"
        raise GeminiError(detail)

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        prompt_feedback = data.get("promptFeedback", {})
        block_reason = prompt_feedback.get("blockReason", "Keine Antwort")
        raise GeminiError(f"Gemini hat die Bilderkennung blockiert ({block_reason})")

    first_cand = candidates[0]
    finish_reason = first_cand.get("finishReason")
    if finish_reason and finish_reason not in ("STOP", "MAX_TOKENS"):
        raise GeminiError(f"Gemini-Verarbeitung abgebrochen ({finish_reason})")

    try:
        text = first_cand["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.warning("Unexpected Gemini response shape: %s", str(data)[:500])
        raise GeminiError("Gemini hat keine verwertbare Antwort geliefert")

    text = text.strip()
    # Extract JSON array using regex in case model wraps it in commentary or markdown
    array_match = re.search(r'\[[\s\S]*\]', text)
    if array_match:
        text = array_match.group(0)
    elif text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Could not parse Gemini JSON: %s", text[:500])
        raise GeminiError("Konnte die Antwort von Gemini nicht als JSON lesen")


async def extract_meal_days(image_bytes: bytes, mime_type: str) -> list[dict]:
    parsed = await _call_gemini_json(PROMPT.format(today=date.today().isoformat()), image_bytes, mime_type)

    days = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        d = item.get("date")
        if not d:
            continue
        d_str = str(d).strip()
        if re.match(r'^\d{2}\.\d{2}\.\d{4}$', d_str):
            parts = d_str.split(".")
            d_str = f"{parts[2]}-{parts[1]}-{parts[0]}"

        # Skip Saturday (5) and Sunday (6)
        try:
            if date.fromisoformat(d_str).weekday() >= 5:
                continue
        except Exception:
            pass

        lines = []
        if item.get("soup"):
            lines.append(f"Suppe: {str(item['soup']).strip()}")
        if item.get("main"):
            lines.append(f"Hauptgericht: {str(item['main']).strip()}")
        elif item.get("meal"):
            lines.append(f"Hauptgericht: {str(item['meal']).strip()}")
        if item.get("vegetarian"):
            lines.append(f"Vegetarisch: {str(item['vegetarian']).strip()}")
        if item.get("muslim"):
            lines.append(f"Muslimisch: {str(item['muslim']).strip()}")
        if item.get("dessert"):
            lines.append(f"Dessert: {str(item['dessert']).strip()}")

        if not lines and item.get("meal"):
            lines.append(str(item["meal"]).strip())

        if lines:
            days.append({"date": d_str, "meal": "\n".join(lines)})

    if not days:
        raise GeminiError("Es konnten keine Tage aus dem Bild erkannt werden")
    return days


async def extract_timetable(image_bytes: bytes, mime_type: str) -> list[dict]:
    parsed = await _call_gemini_json(TIMETABLE_PROMPT, image_bytes, mime_type)

    entries = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        weekday = _WEEKDAY_MAP.get(str(item.get("weekday", "")).strip().lower())
        start = _parse_hhmm(item.get("start_time"))
        end = _parse_hhmm(item.get("end_time"))
        subject = str(item.get("subject") or "").strip()
        if weekday is None or start is None or end is None or not subject or end <= start:
            continue
        short = str(item.get("subject_short") or "").strip()
        entries.append({
            "weekday": weekday,
            "start_time": start,
            "end_time": end,
            "subject": subject,
            "subject_short": short or subject[:4].upper(),
            "room": str(item.get("room") or "").strip() or None,
        })

    if not entries:
        raise GeminiError("Es konnten keine Unterrichtsstunden aus dem Bild erkannt werden")
    entries.sort(key=lambda e: (e["weekday"], e["start_time"]))
    return entries
