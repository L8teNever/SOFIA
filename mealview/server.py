"""Essensplan zum Anschauen - eigener, abgeschotteter Mini-Server (eigener Port).

Bewusst getrennt von Sofia:
- kein Sofia-Code, keine Sofia-Schnittstellen, keine Anmeldung, keine Cookies
- liest Sofias Datenbank NUR lesend (SQLite mode=ro + query_only) und gibt nur
  Datum + Gerichte der aktuellen Woche heraus (kein Foto, keine Namen, keine IDs)
- nur GET/HEAD, nur drei feste Adressen; alles andere 404/405
- strenge Sicherheits-Header (CSP ohne Fremdquellen, kein Einbetten, kein Referrer)

Start: uvicorn mealview.server:app --port 8002 --no-server-header
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

STATIC = Path(__file__).resolve().parent / "static"


def _db_path() -> str:
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/sofia.db")
    return url.split("///", 1)[-1]


# Dateien einmal beim Start lesen - es wird nie etwas vom Dateisystem nach Anfrage ausgeliefert
_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}
_CONTENT = {path: ((STATIC / name).read_bytes(), ctype) for path, (name, ctype) in _FILES.items()}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; manifest-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Robots-Tag": "noindex, nofollow",
}

# ---- einfache Bremse gegen Massenabrufe ----
_hits: dict[str, deque] = defaultdict(deque)


def _too_many(ip: str, limit: int = 120, window: float = 60.0) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    if len(_hits) > 5000:  # Speicher begrenzen
        _hits.clear()
    return False


# ---- Essensplan lesen (nur lesend) ----
CATEGORIES = [
    (re.compile(r"^(tages)?suppe|^vorspeise", re.I), "Suppe", "soup"),
    (re.compile(r"^(men[uü] ?1|hauptgericht|men[uü])$", re.I), "Menü 1", "menu1"),
    (re.compile(r"^(men[uü] ?2|vegetarisch|veggie)$", re.I), "Menü 2", "menu2"),
    (re.compile(r"^(dessert|nachtisch|nachspeise)$", re.I), "Dessert", "dessert"),
]
SKIP = re.compile(r"^(diabetiker|schonkost|vital|muslimisch|halal)", re.I)
ORDER = {"soup": 0, "menu1": 1, "menu2": 2, "dessert": 3, "other": 4}


def _parse_meal(raw: str) -> list[dict[str, str]]:
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([^:]{1,30}):\s*(.+)$", line)
        if m and SKIP.match(m.group(1).strip()):
            continue
        if m:
            key = m.group(1).strip()
            for rx, label, kind in CATEGORIES:
                if rx.match(key):
                    out.append({"label": label, "kind": kind, "text": m.group(2).strip()[:300]})
                    break
            else:
                out.append({"label": key[:30], "kind": "other", "text": m.group(2).strip()[:300]})
        else:
            out.append({"label": "Gericht", "kind": "other", "text": line[:300]})
    out.sort(key=lambda d: ORDER.get(d["kind"], 9))
    return out[:8]


_cache: dict[str, object] = {"at": 0.0, "data": None}


def _read_plan() -> dict:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    con = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only = ON")
        rows = con.execute(
            "SELECT d.date, d.meal FROM meal_plan_days d "
            "WHERE d.plan_id = (SELECT p.id FROM meal_plans p JOIN meal_plan_days x ON x.plan_id = p.id "
            "                   WHERE x.date >= ? GROUP BY p.id ORDER BY p.created_at DESC, p.id DESC LIMIT 1) "
            "AND d.date >= ? AND d.date <= ? ORDER BY d.date",
            (monday.isoformat(), monday.isoformat(), (monday + timedelta(days=11)).isoformat()),
        ).fetchall()
    finally:
        con.close()
    days = []
    for d, meal in rows:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
            continue
        if date.fromisoformat(d).weekday() >= 5:
            continue
        days.append({"date": d, "courses": _parse_meal(meal)})
    return {"today": today.isoformat(), "days": days}


def _plan_cached() -> dict:
    now = time.time()
    if _cache["data"] is None or now - float(_cache["at"]) > 60:
        try:
            _cache["data"] = _read_plan()
        except sqlite3.Error:
            _cache["data"] = {"today": date.today().isoformat(), "days": [], "error": True}
        _cache["at"] = now
    return _cache["data"]  # type: ignore[return-value]


# ---- Antworten ----
def _secure(resp: Response) -> Response:
    for k, v in SECURITY_HEADERS.items():
        resp.headers[k] = v
    return resp


async def static_file(request: Request) -> Response:
    body, ctype = _CONTENT[request.url.path]
    return _secure(Response(body, media_type=ctype, headers={"Cache-Control": "no-cache"}))


async def plan(request: Request) -> Response:
    return _secure(JSONResponse(_plan_cached(), headers={"Cache-Control": "no-store"}))


async def health(request: Request) -> Response:
    return _secure(PlainTextResponse("ok"))


routes = [Route(p, static_file, methods=["GET", "HEAD"]) for p in _CONTENT] + [
    Route("/api/plan", plan, methods=["GET", "HEAD"]),
    Route("/health", health, methods=["GET", "HEAD"]),
]

_inner = Starlette(routes=routes)


async def app(scope, receive, send):
    """Vor allem anderen: nur HTTP, nur GET/HEAD, Bremse, Header auch bei Fehlern."""
    if scope["type"] == "lifespan":
        return await _inner(scope, receive, send)
    if scope["type"] != "http":
        return
    method = scope.get("method", "GET")
    headers = dict(scope.get("headers") or [])
    ip = (headers.get(b"cf-connecting-ip") or b"").decode() or (scope.get("client") or ("?",))[0]
    if method not in ("GET", "HEAD"):
        resp = _secure(PlainTextResponse("Nur Anschauen.", status_code=405, headers={"Allow": "GET, HEAD"}))
        return await resp(scope, receive, send)
    if _too_many(ip):
        return await _secure(PlainTextResponse("Zu viele Anfragen.", status_code=429))(scope, receive, send)
    if scope.get("path") not in _CONTENT and scope.get("path") not in ("/api/plan", "/health"):
        return await _secure(PlainTextResponse("Nicht gefunden.", status_code=404))(scope, receive, send)
    return await _inner(scope, receive, send)
