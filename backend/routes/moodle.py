"""Moodle der eigenen Klasse anzeigen; Zugangsdaten pro Klasse im Admin-Bereich."""
import re
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user, require_admin
from backend.database import get_db
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.services import moodle_service as ms
from backend.services.crypto import encrypt_value

router = APIRouter(prefix="/api/v1", tags=["moodle"])

# Neu laden auf Knopfdruck hoechstens alle 60 s pro Klasse
_last_manual: dict[int, float] = {}


@router.get("/moodle")
async def my_moodle(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if not user.class_id:
        return {"configured": False}
    cls = (await db.execute(select(ClassGroup).where(ClassGroup.id == user.class_id))).scalar_one_or_none()
    if not cls or not ms.credentials(cls):
        return {"configured": False}
    data = ms.cached(cls.id)
    if data is None:
        try:
            data = await ms.refresh_class(cls.id)
        except ms.MoodleError as exc:
            return {"configured": True, "error": str(exc), "courses": []}
    return {"configured": True, **data}


@router.post("/moodle/refresh")
async def refresh_my_moodle(user: User = Depends(get_current_user)):
    if not user.class_id:
        raise HTTPException(400, "Keine Klasse")
    now = time.time()
    if now - _last_manual.get(user.class_id, 0) < 60:
        data = ms.cached(user.class_id) or {}
        return {"configured": True, **data}
    _last_manual[user.class_id] = now
    try:
        data = await ms.refresh_class(user.class_id)
    except ms.MoodleError as exc:
        raise HTTPException(502, str(exc))
    return {"configured": True, **data}


@router.get("/moodle/file")
async def moodle_file(u: str = Query(...), user: User = Depends(get_current_user)):
    """Datei aus Moodle durchreichen - der Token bleibt auf dem Server."""
    if not user.class_id:
        raise HTTPException(404)
    try:
        base, token = await ms.file_token(user.class_id)
    except ms.MoodleError as exc:
        raise HTTPException(502, str(exc))
    if not ms.same_host(u, base):
        raise HTTPException(400, "Kein Moodle-Dateilink")
    url = u.replace("/pluginfile.php", "/webservice/pluginfile.php", 1) if "/webservice/pluginfile.php" not in u else u
    url = re.sub(r"([?&])forcedownload=1(&|$)", r"\1", url).rstrip("?&")
    sep = "&" if "?" in url else "?"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(f"{url}{sep}token={token}")
    if r.status_code != 200:
        raise HTTPException(r.status_code if r.status_code in (403, 404) else 502, "Datei nicht verfügbar")
    ctype = r.headers.get("content-type", "application/octet-stream")
    if "application/json" in ctype:  # Moodle meldet Fehler als JSON
        raise HTTPException(404, "Datei nicht verfügbar")
    headers = {"Cache-Control": "private, max-age=3600"}
    # im Browser anzeigen statt herunterladen
    if r.headers.get("content-disposition"):
        headers["Content-Disposition"] = re.sub(r"^\s*attachment", "inline", r.headers["content-disposition"], flags=re.I)
    # Moodle-HTML (Textseiten) ohne Skripte und ohne Zugriff auf Sofia anzeigen
    if "html" in ctype or "svg" in ctype or "xml" in ctype:
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src * data:; style-src 'unsafe-inline' *; font-src *"
        headers["X-Content-Type-Options"] = "nosniff"
    return Response(content=r.content, media_type=ctype, headers=headers)


# ---- Admin: Zugangsdaten pro Klasse ----
class MoodleCredentials(BaseModel):
    url: str
    username: str
    password: Optional[str] = ""


async def _class_for_admin(class_id: int, db: AsyncSession, user: User) -> ClassGroup:
    if user.role != "super_admin" and user.class_id != class_id:
        raise HTTPException(403, "Not your class")
    cls = (await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))).scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    return cls


@router.get("/classes/{class_id}/moodle")
async def get_moodle_settings(class_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    cls = await _class_for_admin(class_id, db, user)
    data = ms.cached(class_id) or {}
    return {
        "configured": bool(ms.credentials(cls)),
        "url": cls.moodle_url or "",
        "username": cls.moodle_user or "",
        "has_password": bool(cls.moodle_password_enc),
        "updated_at": data.get("updated_at"),
        "courses": len(data.get("courses") or []),
        "error": data.get("error"),
    }


@router.post("/classes/{class_id}/moodle")
async def save_moodle_settings(class_id: int, body: MoodleCredentials, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    cls = await _class_for_admin(class_id, db, user)
    cls.moodle_url = ms.normalize_url(body.url) or None
    cls.moodle_user = body.username.strip() or None
    if body.password:  # leer = altes Passwort behalten
        cls.moodle_password_enc = encrypt_value(body.password)
    if not cls.moodle_url:
        cls.moodle_user = None
        cls.moodle_password_enc = None
    await db.commit()
    ms.forget(class_id)
    return {"ok": True}


@router.post("/classes/{class_id}/moodle/test")
async def test_moodle_settings(class_id: int, body: MoodleCredentials, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    cls = await _class_for_admin(class_id, db, user)
    base = ms.normalize_url(body.url)
    password = body.password
    if not password and cls.moodle_password_enc:
        creds = ms.credentials(cls)
        password = creds[2] if creds else ""
    if not (base and body.username and password):
        return {"ok": False, "message": "Adresse, Benutzer und Passwort angeben"}
    try:
        async with httpx.AsyncClient(timeout=ms.TIMEOUT, follow_redirects=True) as client:
            token = await ms.login(client, base, body.username.strip(), password)
            info = await ms.call(client, base, token, "core_webservice_get_site_info")
            courses = await ms.call(client, base, token, "core_enrol_get_users_courses", userid=info["userid"])
    except ms.MoodleError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"Angemeldet als {info.get('fullname') or body.username} · {len(courses)} Kurse"}


@router.post("/classes/{class_id}/moodle/sync")
async def sync_moodle_now(class_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    await _class_for_admin(class_id, db, user)
    try:
        data = await ms.refresh_class(class_id)
    except ms.MoodleError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"{len(data.get('courses') or [])} Kurse geladen"}
