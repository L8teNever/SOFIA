"""KI im Heft (Gemini): Fragen zu einer markierten Stelle beantworten.

Der Schluessel bleibt hier auf dem Server (GEMINI_API_KEY) und geht nie an den Browser.
Bild und Text der Markierung kommen vom Geraet, die Antwort geht zurueck. Ohne httpx -
das Notes-Venv hat es nicht - also urllib in einem Thread, wie in cloudflare_ocr.py.
Bei 429/503 wird kurz gewartet und zuletzt das Ersatzmodell versucht (wie in Sofias
gemini_client.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("sofianotes.ai")

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "").strip()
BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_SEC = 150  # Modelle mit Denkschritten brauchen bei Bildern auch mal 30-60 s
MAX_IMAGE_CHARS = 8_000_000  # ~6 MB Bild als data-URI
MAX_TEXT_CHARS = 30_000
RETRY_STATUS = (429, 500, 502, 503, 504)
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_models_cache: dict[str, Any] = {"at": 0.0, "list": []}


def configured() -> bool:
    return bool(API_KEY)


def _request(path: str, body: dict | None = None, method: str = "GET") -> dict:
    url = f"{BASE}/{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _models_sync() -> list[dict[str, str]]:
    now = time.time()
    if _models_cache["list"] and now - _models_cache["at"] < 3600:
        return _models_cache["list"]
    out: list[dict[str, str]] = []
    try:
        data = _request("models?pageSize=200")
    except Exception as err:  # noqa: BLE001
        log.warning("Modell-Liste nicht abrufbar: %s", err)
        return _models_cache["list"] or []
    for m in data.get("models") or []:
        name = str(m.get("name") or "").split("/")[-1]
        if not name or "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        # Alte Spezialmodelle (Einbettungen, Bildgenerierung) gehoeren nicht in die Liste
        if any(w in name for w in ("embedding", "aqa", "imagen", "veo", "tts", "native-audio", "image")):
            continue
        out.append({"id": name, "label": str(m.get("displayName") or name)})
    # nur die Gemini-Modelle anbieten (Forschungs-/Agenten-Modelle passen hier nicht)
    gem = [m for m in out if m["id"].startswith("gemini")]
    out = gem or out
    out.sort(key=lambda x: x["id"])
    _models_cache.update({"at": now, "list": out})
    return out


async def models() -> list[dict[str, str]]:
    if not configured():
        return []
    return await asyncio.get_event_loop().run_in_executor(None, _models_sync)


def _valid_model(model: str | None) -> str:
    m = (model or "").strip()
    return m if m and MODEL_RE.match(m) else DEFAULT_MODEL


SYSTEM = (
    "Du hilfst Simon, einem Schueler am Berufskolleg Technik, direkt in seinem digitalen Heft.\n"
    "Er markiert eine Stelle (Handschrift, gedruckten Text, ein Bild oder eine Aufgabe) und fragt dazu.\n"
    "Regeln:\n"
    "- Antworte auf Deutsch, locker und per Du, ohne Floskeln.\n"
    "- Fasse dich kurz: nur so lang wie noetig, kein Vorgeplaenkel.\n"
    "- Das Bild zeigt die markierte Stelle. Beziehe dich darauf.\n"
    "- Sollst du Text schreiben oder verbessern, gib nur den Text aus, ohne Erklaerung drumherum.\n"
    "- Rechnungen: zeige den Rechenweg kurz in einzelnen Schritten.\n"
    "- Bist du dir nicht sicher, sag das statt zu raten.\n"
    "- Kein Markdown mit # oder **, das Heft zeigt reinen Text."
)


def _parts_for(turn: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    img = turn.get("image")
    if isinstance(img, str) and img.startswith("data:image/") and len(img) <= MAX_IMAGE_CHARS:
        head, _, b64 = img.partition(",")
        mime = head[5:].split(";")[0] or "image/jpeg"
        if b64:
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    text = str(turn.get("text") or "")[:MAX_TEXT_CHARS]
    if text:
        parts.append({"text": text})
    return parts


def _ask_sync(contents: list[dict[str, Any]], model: str) -> dict:
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    tries = [model] + ([FALLBACK_MODEL] if FALLBACK_MODEL and FALLBACK_MODEL != model else [])
    last_err = "unknown"
    for mi, m in enumerate(tries):
        for attempt in range(3):
            try:
                data = _request(f"models/{m}:generateContent", body, "POST")
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:400]
                last_err = f"{err.code}: {detail}"
                if err.code in RETRY_STATUS and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if err.code in RETRY_STATUS and mi < len(tries) - 1:
                    break  # naechstes Modell
                return {"ok": False, "error": "api", "detail": last_err, "status": err.code}
            except Exception as err:  # noqa: BLE001
                last_err = str(err)[:200]
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                return {"ok": False, "error": "network", "detail": last_err}
            cand = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text") or "" for p in (cand.get("content") or {}).get("parts") or [])
            if not text.strip():
                reason = cand.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason")
                return {"ok": False, "error": "empty", "detail": str(reason or "")}
            return {"ok": True, "text": text.strip(), "model": m}
    return {"ok": False, "error": "api", "detail": last_err}


async def ask(turns: list[dict[str, Any]], model: str | None = None) -> dict:
    """turns: [{role: 'user'|'model', text, image?}] - neuester Eintrag zuletzt."""
    if not configured():
        return {"ok": False, "error": "not_configured"}
    contents = []
    for t in turns[-12:]:
        parts = _parts_for(t)
        if not parts:
            continue
        contents.append({"role": "model" if t.get("role") == "model" else "user", "parts": parts})
    if not contents:
        return {"ok": False, "error": "empty_prompt"}
    return await asyncio.get_event_loop().run_in_executor(None, _ask_sync, contents, _valid_model(model))
