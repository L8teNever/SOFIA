"""Formularfelder auf Arbeitsblaettern finden (CommonForms / FFDNet-S, YOLO11s als ONNX).

Erkennt drei Arten: Textfeld, Ankreuzkaestchen, Unterschrift. Laeuft nur hier im eigenen
Container auf der CPU (ca. 1 s pro Seite). Das Ergebnis haengt nur am Bild - Bilder aendern
sich nie (neue Fassung = neue ID) - und wird deshalb neben dem Bild gespeichert und nur einmal
berechnet. Das Modell (38 MB) liegt in data/models und wird beim ersten Bedarf geladen.

Modell: https://huggingface.co/jbarrow/FFDNet-S-cpu (Paper: arXiv 2509.16506)
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Any

from . import media

log = logging.getLogger("sofianotes.forms")

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "ffdnet"
MODEL_PATH = MODEL_DIR / "FFDNet-S.onnx"
MODEL_URL = os.environ.get(
    "FORMS_MODEL_URL", "https://huggingface.co/jbarrow/FFDNet-S-cpu/resolve/main/FFDNet-S.onnx"
)
ENABLED = os.environ.get("FORMS_DISABLED", "").strip().lower() not in ("1", "true", "yes")
# Fassung der Auswertung: aendert sich etwas an Schwellen/Nachbearbeitung, wird neu gerechnet
VERSION = 1

SIZE = 1216
CONF = 0.3
KINDS = ("text", "check", "sign")

_lock = threading.Lock()
_session = None
_failed = False


def _fields_path(media_id: str) -> Path:
    return media.MEDIA_DIR / f"{media_id}.fields.json"


def _download() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".part")
    log.info("lade Formular-Modell von %s", MODEL_URL)
    with urllib.request.urlopen(MODEL_URL, timeout=120) as r, open(tmp, "wb") as f:  # noqa: S310
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    if tmp.stat().st_size < 1_000_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("model download too small")
    tmp.replace(MODEL_PATH)


def _get_session():
    global _session, _failed
    if _session is not None or _failed:
        return _session
    try:
        import onnxruntime as ort

        if not MODEL_PATH.is_file():
            _download()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        _session = ort.InferenceSession(str(MODEL_PATH), sess_options=opts, providers=["CPUExecutionProvider"])
    except Exception:  # noqa: BLE001
        log.exception("Formular-Erkennung nicht verfuegbar")
        _failed = True
    return _session


def available() -> bool:
    return ENABLED and not _failed


def _nms(boxes, scores, iou: float):
    import numpy as np

    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        a_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        a_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        ov = inter / np.maximum(1e-9, a_i + a_r - inter)
        # auch fast ganz enthaltene Kaestchen zaehlen als doppelt
        inside = inter / np.maximum(1e-9, np.minimum(a_i, a_r))
        order = rest[(ov < iou) & (inside < 0.85)]
    return keep


def detect_bytes(blob: bytes) -> list[dict[str, Any]]:
    """Felder auf einem Bild: Rechtecke relativ zur Bildgroesse (0..1), sortiert in Leserichtung."""
    import numpy as np
    from PIL import Image

    sess = _get_session()
    if sess is None:
        raise RuntimeError("model unavailable")
    im = Image.open(io.BytesIO(blob)).convert("RGB")
    W, H = im.size
    r = min(SIZE / W, SIZE / H)
    nw, nh = max(1, round(W * r)), max(1, round(H * r))
    px, py = (SIZE - nw) // 2, (SIZE - nh) // 2
    canvas = Image.new("RGB", (SIZE, SIZE), (114, 114, 114))
    canvas.paste(im.resize((nw, nh), Image.BILINEAR), (px, py))
    x = (np.asarray(canvas, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
    with _lock:
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]  # (7, N): cx cy w h + 3 Klassen
    cls_scores = out[4:]
    scores = cls_scores.max(0)
    cls = cls_scores.argmax(0)
    m = scores >= CONF
    if not m.any():
        return []
    cx, cy, w, h = out[0][m], out[1][m], out[2][m], out[3][m]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    scores, cls = scores[m], cls[m]
    keep = _nms(boxes, scores, 0.45)
    fields = []
    for i in keep:
        x0 = (boxes[i, 0] - px) / nw
        y0 = (boxes[i, 1] - py) / nh
        x1 = (boxes[i, 2] - px) / nw
        y1 = (boxes[i, 3] - py) / nh
        x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in (x0, y0, x1, y1))
        if x1 - x0 < 0.004 or y1 - y0 < 0.003:
            continue
        fields.append({
            "t": KINDS[int(cls[i])],
            "x0": round(x0, 5),
            "y0": round(y0, 5),
            "x1": round(x1, 5),
            "y1": round(y1, 5),
            "c": round(float(scores[i]), 3),
        })
    # Leserichtung: Zeilen von oben nach unten, in der Zeile von links nach rechts
    fields.sort(key=lambda f: (f["y0"] + f["y1"]) / 2)
    rows: list[list[dict[str, Any]]] = []
    for f in fields:
        mid = (f["y0"] + f["y1"]) / 2
        if rows:
            last = rows[-1]
            ref = sum((g["y0"] + g["y1"]) / 2 for g in last) / len(last)
            hgt = max(g["y1"] - g["y0"] for g in last + [f])
            if abs(mid - ref) < hgt * 0.6:
                last.append(f)
                continue
        rows.append([f])
    return [f for row in rows for f in sorted(row, key=lambda g: g["x0"])]


def fields_for(media_id: str) -> list[dict[str, Any]] | None:
    """Gespeichertes Ergebnis oder neu berechnen. None = Bild fehlt."""
    path = _fields_path(media_id)
    if path.is_file():
        try:
            cached = json.loads(path.read_text("utf-8"))
            if cached.get("v") == VERSION:
                return cached.get("fields") or []
        except Exception:  # noqa: BLE001
            pass
    blob = media.load_bytes(media_id)
    if blob is None:
        return None
    fields = detect_bytes(blob)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": VERSION, "fields": fields}), "utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return fields
