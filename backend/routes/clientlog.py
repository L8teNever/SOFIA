"""Fehlerprotokoll der App: Fehler, die ein Geraet (PWA/Browser) bemerkt, gehen automatisch
hierher und landen als Logdatei auf dem Server - eine Datei pro Tag unter
<data>/logs/client-errors-JJJJ-MM-TT.jsonl (eine JSON-Zeile pro Fehler, 30 Tage).

Bewusst ohne Ansicht in der App/Admin-Konsole: nachlesen nur direkt auf dem Server.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import _data_dir
from backend.database import get_db

router = APIRouter(prefix="/api/v1", tags=["client-log"])

LOG_DIR = os.getenv("CLIENT_LOG_DIR") or os.path.join(_data_dir(), "logs")
KEEP_DAYS = 30
MAX_FILE_BYTES = 20_000_000  # pro Tag; danach wird fuer den Tag nichts mehr angehaengt
_lock = threading.Lock()
_recent: dict[str, deque] = defaultdict(deque)
_last_cleanup = 0.0


def _clip(v, n: int):
    return v[:n] if isinstance(v, str) else v


def _allowed(key: str) -> bool:
    """Hoechstens 40 Meldungen pro Minute und Nutzer/Adresse (eine Fehlerschleife flutet nichts)."""
    now = time.time()
    q = _recent[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 40:
        return False
    q.append(now)
    return True


def _cleanup() -> None:
    global _last_cleanup
    if time.time() - _last_cleanup < 3600:
        return
    _last_cleanup = time.time()
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    try:
        for name in os.listdir(LOG_DIR):
            if name.startswith("client-errors-") and name[14:24] < cutoff:
                os.remove(os.path.join(LOG_DIR, name))
    except OSError:
        pass


def _write(entry: dict) -> None:
    path = os.path.join(LOG_DIR, f"client-errors-{datetime.now():%Y-%m-%d}.jsonl")
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        os.makedirs(LOG_DIR, exist_ok=True)
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                return
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        _cleanup()


@router.post("/client-log")
async def client_log(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    # Auch ohne Anmeldung annehmen (Fehler koennen vor dem Login passieren)
    user = None
    try:
        user = await get_current_user(request, db)
    except Exception:
        pass
    ip = request.headers.get("Cf-Connecting-Ip") or (request.client.host if request.client else "?")
    if not _allowed(f"u{user.id}" if user else f"ip{ip}"):
        return {"ok": False}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    actions = body.get("actions")
    entry = {
        "serverTime": datetime.now().isoformat(timespec="seconds"),
        "clientTime": _clip(body.get("clientTime"), 40),
        "user": ({"id": user.id, "email": user.email, "name": getattr(user, "display_name", None)} if user else None),
        "ip": ip,
        "kind": _clip(body.get("kind"), 30) or "error",
        "message": _clip(body.get("message"), 800),
        "stack": _clip(body.get("stack"), 4000),
        "where": body.get("where") if isinstance(body.get("where"), dict) else {},
        "version": _clip(body.get("version"), 60),
        "standalone": bool(body.get("standalone")),
        "online": body.get("online"),
        "ua": _clip(body.get("ua") or request.headers.get("user-agent"), 300),
        "actions": [_clip(a, 160) for a in actions[-30:]] if isinstance(actions, list) else [],
    }
    _write(entry)
    return {"ok": True}
