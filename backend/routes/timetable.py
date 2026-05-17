from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.config import settings
from cryptography.fernet import Fernet
import httpx
import asyncio
from datetime import date, timedelta

router = APIRouter(prefix="/api/v1/timetable", tags=["timetable"])

def decrypt_password(enc: str) -> str:
    key = settings.encryption_key
    if not key:
        return enc
    return Fernet(key.encode()).decrypt(enc.encode()).decode()

def date_int(d: date) -> int:
    return int(d.strftime("%Y%m%d"))

def parse_lessons(raw: list) -> list:
    result = []
    for l in raw:
        su = l.get("su") or []
        te = l.get("te") or []
        ro = l.get("ro") or []
        code = l.get("code", 0)
        result.append({
            "date": str(l.get("date", "")),
            "startTime": l.get("startTime", 0),
            "endTime": l.get("endTime", 0),
            "subject": (su[0].get("longName") or su[0].get("name") or "") if su else "",
            "subject_short": su[0].get("name", "") if su else "",
            "teacher": te[0].get("name", "") if te else "",
            "room": ro[0].get("name", "") if ro else "",
            "cancelled": code == 1,
            "substituted": code == 2,
        })
    return sorted(result, key=lambda x: (x["date"], x["startTime"]))

async def untis_get_class_id(client: httpx.AsyncClient, base: str, school: str,
                              cookies: dict, class_name: str) -> tuple[int | None, list[str]]:
    """Returns (class_id, debug_names). Tries multiple strategies."""
    name_clean = class_name.strip().lower()

    # Strategy 1: REST app/data — gives logged-in user's own classInfos directly
    try:
        r = await client.get(
            f"{base}/WebUntis/api/rest/view/v1/app/data",
            cookies=cookies, headers={"school": school},
        )
        if r.status_code == 200:
            data = r.json()
            user = data.get("user") or data.get("data", {}).get("user", {})
            class_infos = user.get("classInfos") or user.get("klassenInfos") or []
            names = [c.get("name", c.get("className", "")) for c in class_infos]
            for c in class_infos:
                cname = c.get("name") or c.get("className") or ""
                if cname.strip().lower() == name_clean:
                    cid = c.get("id") or c.get("classId")
                    if cid:
                        return cid, names
            if names:
                return None, names  # found infos but name mismatch
    except Exception:
        pass

    # Strategy 2: getClasses JSON-RPC with and without schoolyear
    syear_resp = await client.post(
        f"{base}/WebUntis/jsonrpc.do?school={school}",
        json={"id": "sy", "method": "getCurrentSchoolyear", "params": {}, "jsonrpc": "2.0"},
        cookies=cookies,
    )
    syear_id = syear_resp.json().get("result", {}).get("id")

    all_names: list[str] = []
    for params in ([{"schoolyearId": syear_id}] if syear_id else []) + [{}]:
        resp = await client.post(
            f"{base}/WebUntis/jsonrpc.do?school={school}",
            json={"id": "cls", "method": "getClasses", "params": params, "jsonrpc": "2.0"},
            cookies=cookies,
        )
        classes = resp.json().get("result", [])
        if classes:
            all_names = [c.get("name", "") for c in classes]
            for c in classes:
                if c.get("name", "").strip().lower() == name_clean:
                    return c["id"], all_names
            break

    return None, all_names

async def untis_get_timetable(client: httpx.AsyncClient, base: str, school: str,
                               cookies: dict, class_id: int, start: date, end: date) -> list:
    resp = await client.post(
        f"{base}/WebUntis/jsonrpc.do?school={school}",
        json={"id": "tt", "method": "getTimetable",
              "params": {"id": class_id, "type": 1,
                         "startDate": date_int(start), "endDate": date_int(end)},
              "jsonrpc": "2.0"},
        cookies=cookies,
    )
    return resp.json().get("result", [])

@router.get("/")
async def get_timetable(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        raise HTTPException(400, "No class assigned")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == current_user.class_id))
    cls = result.scalar_one_or_none()
    if not cls or not cls.untis_url:
        return {"configured": False}

    try:
        password = decrypt_password(cls.untis_password_enc) if cls.untis_password_enc else ""
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        next_monday = this_monday + timedelta(days=7)

        base = cls.untis_url.rstrip("/")
        school = cls.untis_school

        async with httpx.AsyncClient(timeout=15) as client:
            login = await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "1", "method": "authenticate",
                      "params": {"user": cls.untis_user, "password": password, "client": "sofia"},
                      "jsonrpc": "2.0"}
            )
            login_data = login.json()
            if "error" in login_data:
                return {"configured": True, "error": "Login fehlgeschlagen"}
            session_id = login_data["result"]["sessionId"]
            cookies = {"JSESSIONID": session_id}

            untis_class_id, found_names = await untis_get_class_id(client, base, school, cookies, cls.untis_class or "")
            if not untis_class_id:
                await client.post(f"{base}/WebUntis/jsonrpc.do?school={school}",
                    json={"id": "x", "method": "logout", "params": {}, "jsonrpc": "2.0"}, cookies=cookies)
                hint = f" Verfügbare Klassen: {', '.join(found_names[:20])}" if found_names else " Keine Klassen gefunden."
                return {"configured": True, "error": f"Klasse '{cls.untis_class}' nicht gefunden.{hint}"}

            this_raw, next_raw = await asyncio.gather(
                untis_get_timetable(client, base, school, cookies, untis_class_id,
                                    this_monday, this_monday + timedelta(days=4)),
                untis_get_timetable(client, base, school, cookies, untis_class_id,
                                    next_monday, next_monday + timedelta(days=4)),
            )

            await client.post(f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "2", "method": "logout", "params": {}, "jsonrpc": "2.0"}, cookies=cookies)

        return {
            "configured": True,
            "this_week": {"start": this_monday.isoformat(), "lessons": parse_lessons(this_raw)},
            "next_week": {"start": next_monday.isoformat(), "lessons": parse_lessons(next_raw)},
        }
    except Exception as e:
        return {"configured": True, "error": str(e)}
