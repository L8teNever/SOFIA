"""Gedruckten Text auf Bildern und PDF-Seiten finden (Tesseract, Deutsch + Englisch).

Liefert Zeilen mit ihren Woertern als Kaesten (0..1 im Bild). Damit rastet der Textmarker
auf den gedruckten Text ein. Laeuft lokal im Container auf der CPU (ca. 1-3 s pro Seite).
Bilder aendern sich nie (neue Fassung = neue ID), deshalb wird das Ergebnis neben dem Bild
gespeichert und nur einmal berechnet - wie bei den Formularfeldern (forms.py).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import media

log = logging.getLogger("sofianotes.ocr_text")

ENABLED = os.environ.get("TEXT_OCR_DISABLED", "").strip().lower() not in ("1", "true", "yes")
LANGS = os.environ.get("TEXT_OCR_LANGS", "deu+eng").strip() or "deu+eng"
# Fassung der Auswertung: aendert sich etwas an Schwellen/Gruppierung, wird neu gerechnet
VERSION = 1
MIN_CONF = 35  # Woerter darunter sind meist Rauschen (Linien, Bildteile)
MIN_SIDE = 1800  # kleinere Bilder vorher hochrechnen, Tesseract mag ~300 dpi
MAX_SIDE = 4000


def _text_path(media_id: str) -> Path:
    return media.MEDIA_DIR / f"{media_id}.text.json"


def available() -> bool:
    return ENABLED and shutil.which("tesseract") is not None


def _prepare_png(blob: bytes) -> tuple[bytes, int, int]:
    from PIL import Image, ImageOps

    try:
        from pillow_heif import register_heif_opener  # HEIC vom iPhone

        register_heif_opener()
    except Exception:  # noqa: BLE001
        pass
    im = Image.open(io.BytesIO(blob))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im = im.convert("L")
    W, H = im.size
    long_side = max(W, H)
    f = 1.0
    if long_side < MIN_SIDE:
        f = MIN_SIDE / long_side
    elif long_side > MAX_SIDE:
        f = MAX_SIDE / long_side
    if f != 1.0:
        im = im.resize((max(1, round(W * f)), max(1, round(H * f))), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue(), im.size[0], im.size[1]


def parse_tsv(tsv: str, W: int, H: int) -> list[dict[str, Any]]:
    """Tesseract-TSV -> Zeilen [{x0,y0,x1,y1, w:[{x0,y0,x1,y1,t}]}], Koordinaten 0..1."""
    lines: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
            left, top, wd, ht = (int(row[k]) for k in ("left", "top", "width", "height"))
        except (TypeError, ValueError, KeyError):
            continue
        if not text or conf < MIN_CONF or wd <= 0 or ht <= 0:
            continue
        # einzelne Sonderzeichen sind fast immer Striche/Kaesten, keine Woerter
        if len(text) == 1 and not text.isalnum():
            continue
        key = (row.get("page_num", ""), row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
        lines.setdefault(key, []).append({
            "x0": round(left / W, 5),
            "y0": round(top / H, 5),
            "x1": round((left + wd) / W, 5),
            "y1": round((top + ht) / H, 5),
            "t": text,
        })
    out = []
    for words in lines.values():
        words.sort(key=lambda w: w["x0"])
        out.append({
            "x0": min(w["x0"] for w in words),
            "y0": min(w["y0"] for w in words),
            "x1": max(w["x1"] for w in words),
            "y1": max(w["y1"] for w in words),
            "w": words,
        })
    out.sort(key=lambda l: ((l["y0"] + l["y1"]) / 2, l["x0"]))
    return out


def detect_bytes(blob: bytes) -> list[dict[str, Any]]:
    png, W, H = _prepare_png(blob)
    res = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", LANGS, "--psm", "3", "--dpi", "300", "tsv"],
        input=png,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError("tesseract: " + res.stderr.decode("utf-8", "replace")[-300:])
    return parse_tsv(res.stdout.decode("utf-8", "replace"), W, H)


def text_for(media_id: str, force: bool = False) -> list[dict[str, Any]] | None:
    """Gespeichertes Ergebnis oder neu berechnen (force: immer neu). None = Bild fehlt."""
    path = _text_path(media_id)
    if path.is_file() and not force:
        try:
            cached = json.loads(path.read_text("utf-8"))
            if cached.get("v") == VERSION:
                return cached.get("lines") or []
        except Exception:  # noqa: BLE001
            pass
    blob = media.load_bytes(media_id)
    if blob is None:
        return None
    lines = detect_bytes(blob)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": VERSION, "lines": lines}, ensure_ascii=False), "utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return lines
