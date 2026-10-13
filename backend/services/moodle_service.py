"""Moodle pro Klasse: ein Konto meldet sich fuer die ganze Klasse an.

Die Zugangsdaten stehen (Passwort verschluesselt) an der Klasse und werden
nur im Admin-Bereich eingetragen - nie im Code. Ein Hintergrund-Lauf holt
regelmaessig Kurse, Kursinhalte, Ankuendigungen und Aufgaben und legt das
Ergebnis als JSON neben die Datenbank, damit die Seite sofort etwas zeigt
(auch direkt nach einem Neustart).

Dateien aus Moodle (pluginfile.php) brauchen den Token des Kontos. Der
verlaesst den Server nie: Links zeigen auf /api/v1/moodle/file, das die
Datei mit Token holt und durchreicht.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.class_group import ClassGroup
from backend.services.crypto import decrypt_value

logger = logging.getLogger(__name__)

TIMEOUT = 20
REFRESH_SECONDS = 20 * 60
SERVICE = "moodle_mobile_app"
CACHE_DIR = os.environ.get("MOODLE_CACHE_DIR", "data")

_tokens: dict[int, tuple[str, str]] = {}  # class_id -> (base_url, token)
_cache: dict[int, dict[str, Any]] = {}
_locks: dict[int, asyncio.Lock] = {}


class MoodleError(Exception):
    pass


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    return url


def _plain(text: str | None, limit: int = 600) -> str:
    """HTML aus Moodle als kurzer, lesbarer Text."""
    if not text:
        return ""
    t = re.sub(r"<(br|/p|/div|/li|/h\d)\s*/?>", "\n", text, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t).strip()
    return t[:limit] + ("…" if len(t) > limit else "")


async def login(client: httpx.AsyncClient, base: str, username: str, password: str) -> str:
    try:
        r = await client.post(f"{base}/login/token.php", data={"username": username, "password": password, "service": SERVICE})
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MoodleError("Moodle nicht erreichbar") from exc
    if "token" not in data:
        raise MoodleError(data.get("error") or "Anmeldung fehlgeschlagen")
    return data["token"]


async def call(client: httpx.AsyncClient, base: str, token: str, function: str, **params: Any) -> Any:
    payload = {"wstoken": token, "wsfunction": function, "moodlewsrestformat": "json", **params}
    try:
        r = await client.post(f"{base}/webservice/rest/server.php", data=payload)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MoodleError(f"{function}: Anfrage fehlgeschlagen") from exc
    if isinstance(data, dict) and "exception" in data:
        raise MoodleError(f"{function}: {data.get('message') or data['exception']}")
    return data


def _file_link(url: str | None) -> str | None:
    """Moodle-Dateilink -> eigener Proxy-Link (ohne Token)."""
    if not url:
        return None
    return "/api/v1/moodle/file?u=" + quote(url, safe="")


def _ftime(f: dict[str, Any]) -> int:
    """Hochgeladen am: Moodle liefert timecreated/timemodified (Sekunden)."""
    return int(f.get("timecreated") or f.get("timemodified") or 0)


def _module(m: dict[str, Any]) -> dict[str, Any]:
    files = []
    for c in m.get("contents") or []:
        if c.get("type") == "file" and c.get("fileurl"):
            files.append({"name": c.get("filename") or "Datei", "size": c.get("filesize") or 0, "mime": c.get("mimetype") or "", "url": _file_link(c.get("fileurl")), "time": _ftime(c)})
        elif c.get("type") == "url" and c.get("fileurl"):
            files.append({"name": c.get("filename") or c.get("fileurl"), "size": 0, "mime": "link", "url": c.get("fileurl"), "external": True, "time": _ftime(c)})
    # Wann eingestellt: Moodle 4 liefert "added", sonst die aelteste Datei des Eintrags
    added = int(m.get("added") or 0)
    if not added:
        times = [f["time"] for f in files if f.get("time")]
        added = min(times) if times else 0
    return {
        "id": m.get("id"),
        "name": html.unescape(m.get("name") or ""),
        "type": m.get("modname") or "",
        "url": m.get("url"),
        "text": _plain(m.get("description")),
        "files": files[:20],
        "visible": bool(m.get("uservisible", True)),
        "time": added,
    }


async def fetch_all(base: str, username: str, password: str, token: str | None = None) -> tuple[dict[str, Any], str]:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        if not token:
            token = await login(client, base, username, password)
        try:
            info = await call(client, base, token, "core_webservice_get_site_info")
        except MoodleError:
            # Token abgelaufen/ungueltig: einmal neu anmelden
            token = await login(client, base, username, password)
            info = await call(client, base, token, "core_webservice_get_site_info")
        user_id = info["userid"]
        courses = await call(client, base, token, "core_enrol_get_users_courses", userid=user_id)

        out_courses = []
        for c in courses:
            cid = c["id"]
            sections = []
            try:
                contents = await call(client, base, token, "core_course_get_contents", courseid=cid)
                for s in contents:
                    mods = [_module(m) for m in s.get("modules") or [] if m.get("uservisible", True)]
                    summary = _plain(s.get("summary"))
                    if not mods and not summary:
                        continue
                    sections.append({"name": html.unescape(s.get("name") or ""), "text": summary, "modules": mods})
            except MoodleError as exc:
                logger.info("Moodle Kursinhalt %s: %s", cid, exc)
            out_courses.append({
                "id": cid,
                "name": html.unescape(c.get("fullname") or c.get("shortname") or ""),
                "short": c.get("shortname") or "",
                "url": f"{base}/course/view.php?id={cid}",
                "lastaccess": c.get("lastaccess") or 0,
                "sections": sections,
                "news": [],
                "assignments": [],
            })
        by_id = {c["id"]: c for c in out_courses}

        # Aufgaben aller Kurse auf einmal
        if out_courses:
            params = {f"courseids[{i}]": c["id"] for i, c in enumerate(out_courses)}
            try:
                res = await call(client, base, token, "mod_assign_get_assignments", **params)
                for course in res.get("courses") or []:
                    tgt = by_id.get(course.get("id"))
                    if not tgt:
                        continue
                    for a in course.get("assignments") or []:
                        tgt["assignments"].append({
                            "id": a.get("id"),
                            "cmid": a.get("cmid"),
                            "name": html.unescape(a.get("name") or ""),
                            "due": a.get("duedate") or 0,
                            "text": _plain(a.get("intro"), 400),
                            "url": f"{base}/mod/assign/view.php?id={a.get('cmid')}" if a.get("cmid") else None,
                            "files": [{"name": f.get("filename") or "Datei", "url": _file_link(f.get("fileurl")), "size": f.get("filesize") or 0, "time": _ftime(f)} for f in a.get("introattachments") or [] if f.get("fileurl")],
                            # eingestellt: ab wann abgegeben werden darf, sonst letzte Aenderung
                            "time": int(a.get("allowsubmissionsfromdate") or a.get("timemodified") or 0),
                        })
            except MoodleError as exc:
                logger.info("Moodle Aufgaben: %s", exc)

            # Ankuendigungen/Foren: die neuesten Beitraege je Forum
            try:
                forums = await call(client, base, token, "mod_forum_get_forums_by_courses", **params)
                for f in forums or []:
                    tgt = by_id.get(f.get("course"))
                    if not tgt:
                        continue
                    try:
                        d = await call(client, base, token, "mod_forum_get_forum_discussions", forumid=f["id"], sortorder=1, page=0, perpage=5)
                        discussions = d.get("discussions") or []
                    except MoodleError:
                        try:
                            d = await call(client, base, token, "mod_forum_get_forum_discussions_paginated", forumid=f["id"], sortby="timemodified", sortdirection="DESC", page=0, perpage=5)
                            discussions = d.get("discussions") or []
                        except MoodleError:
                            continue
                    for p in discussions:
                        tgt["news"].append({
                            "forum": html.unescape(f.get("name") or ""),
                            "title": html.unescape(p.get("name") or p.get("subject") or ""),
                            "text": _plain(p.get("message"), 800),
                            "author": p.get("userfullname") or "",
                            "time": p.get("timemodified") or p.get("created") or 0,
                            "posted": p.get("created") or p.get("timemodified") or 0,
                            "url": f"{base}/mod/forum/discuss.php?d={p.get('discussion') or p.get('id')}",
                            "files": [{"name": a.get("filename") or "Datei", "url": _file_link(a.get("fileurl")), "size": a.get("filesize") or 0, "time": _ftime(a)} for a in p.get("attachments") or [] if a.get("fileurl")],
                        })
            except MoodleError as exc:
                logger.info("Moodle Foren: %s", exc)

        for c in out_courses:
            c["news"].sort(key=lambda n: -n["time"])
            c["news"] = c["news"][:8]
            c["assignments"].sort(key=lambda a: (a["due"] == 0, a["due"]))
        # zuletzt besuchte Kurse zuerst
        out_courses.sort(key=lambda c: -c["lastaccess"])
        return {
            "updated_at": int(time.time()),
            "site": info.get("sitename") or "",
            "account": info.get("fullname") or username,
            "base": base,
            "courses": out_courses,
        }, token


def _cache_path(class_id: int) -> str:
    return os.path.join(CACHE_DIR, f"moodle_cache_{class_id}.json")


def cached(class_id: int) -> dict[str, Any] | None:
    if class_id in _cache:
        return _cache[class_id]
    try:
        with open(_cache_path(class_id), encoding="utf-8") as fh:
            _cache[class_id] = json.load(fh)
            return _cache[class_id]
    except (OSError, ValueError):
        return None


def _store(class_id: int, data: dict[str, Any]) -> None:
    _cache[class_id] = data
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(class_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(class_id))
    except OSError:
        logger.warning("Moodle-Cache konnte nicht gespeichert werden", exc_info=True)


def credentials(cls: ClassGroup) -> tuple[str, str, str] | None:
    if not (cls.moodle_url and cls.moodle_user and cls.moodle_password_enc):
        return None
    try:
        pw = decrypt_value(cls.moodle_password_enc)
    except Exception:  # noqa: BLE001
        logger.warning("Moodle-Passwort der Klasse %s laesst sich nicht entschluesseln", cls.id)
        return None
    return normalize_url(cls.moodle_url), cls.moodle_user, pw


async def refresh_class(class_id: int) -> dict[str, Any]:
    """Holt alles neu (eine Abfrage pro Klasse gleichzeitig)."""
    lock = _locks.setdefault(class_id, asyncio.Lock())
    async with lock:
        async with AsyncSessionLocal() as db:
            cls = (await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))).scalar_one_or_none()
        creds = credentials(cls) if cls else None
        if not creds:
            raise MoodleError("Moodle ist für diese Klasse nicht eingerichtet")
        base, user, pw = creds
        prev = _tokens.get(class_id)
        token = prev[1] if prev and prev[0] == base else None
        try:
            data, token = await fetch_all(base, user, pw, token)
        except MoodleError as exc:
            old = cached(class_id)
            if old is not None:
                old = {**old, "error": str(exc)}
                _cache[class_id] = old
            raise
        _tokens[class_id] = (base, token)
        _store(class_id, data)
        return data


def forget(class_id: int) -> None:
    """Zugangsdaten geaendert: Token und Cache verwerfen."""
    _tokens.pop(class_id, None)
    _cache.pop(class_id, None)
    try:
        os.remove(_cache_path(class_id))
    except OSError:
        pass


async def file_token(class_id: int) -> tuple[str, str]:
    """Basis-URL und Token fuer den Datei-Proxy (meldet sich bei Bedarf an)."""
    t = _tokens.get(class_id)
    if t:
        return t
    async with AsyncSessionLocal() as db:
        cls = (await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))).scalar_one_or_none()
    creds = credentials(cls) if cls else None
    if not creds:
        raise MoodleError("Moodle ist für diese Klasse nicht eingerichtet")
    base, user, pw = creds
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        token = await login(client, base, user, pw)
    _tokens[class_id] = (base, token)
    return base, token


def same_host(url: str, base: str) -> bool:
    try:
        return urlparse(url).netloc.lower() == urlparse(base).netloc.lower() and "/pluginfile.php" in urlparse(url).path
    except ValueError:
        return False


async def moodle_poll_loop() -> None:
    """Laeuft die ganze Zeit: alle eingerichteten Klassen regelmaessig neu holen."""
    await asyncio.sleep(30)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                ids = [c.id for c in (await db.execute(select(ClassGroup))).scalars().all() if c.moodle_url and c.moodle_user and c.moodle_password_enc]
            for cid in ids:
                try:
                    await refresh_class(cid)
                except MoodleError as exc:
                    logger.info("Moodle Klasse %s: %s", cid, exc)
                except Exception:  # noqa: BLE001
                    logger.warning("Moodle Klasse %s: unerwarteter Fehler", cid, exc_info=True)
        except Exception:  # noqa: BLE001
            logger.warning("Moodle-Lauf fehlgeschlagen", exc_info=True)
        await asyncio.sleep(REFRESH_SECONDS)
