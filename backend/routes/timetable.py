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

class UntisSession:
    def __init__(self, client: httpx.AsyncClient, cookies: dict,
                 person_id: int, person_type: int):
        self.client = client
        self.cookies = cookies
        self.person_id = person_id
        self.person_type = person_type

async def untis_login(base: str, school: str, username: str, password: str) -> UntisSession:
    client = httpx.AsyncClient(timeout=15, verify=False)
    login = await client.post(
        f"{base}/WebUntis/jsonrpc.do?school={school}",
        json={"id": "1", "method": "authenticate",
              "params": {"user": username, "password": password, "client": "sofia"},
              "jsonrpc": "2.0"}
    )
    data = login.json()
    if "error" in data:
        await client.aclose()
        raise ValueError("Login fehlgeschlagen")
    result = data["result"]
    cookies = {"JSESSIONID": result["sessionId"]}
    person_id   = result.get("personId", 0)
    person_type = result.get("personType", 5)
    return UntisSession(client, cookies, person_id, person_type)

async def untis_logout(sess: UntisSession, base: str, school: str):
    try:
        await sess.client.post(f"{base}/WebUntis/jsonrpc.do?school={school}",
            json={"id": "x", "method": "logout", "params": {}, "jsonrpc": "2.0"},
            cookies=sess.cookies)
    finally:
        await sess.client.aclose()

async def untis_get_timetable(sess: UntisSession, base: str, school: str,
                               start: date, end: date) -> list:
    resp = await sess.client.post(
        f"{base}/WebUntis/jsonrpc.do?school={school}",
        json={"id": "tt", "method": "getTimetable",
              "params": {"id": sess.person_id, "type": sess.person_type,
                         "startDate": date_int(start), "endDate": date_int(end)},
              "jsonrpc": "2.0"},
        cookies=sess.cookies,
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

        sess = await untis_login(base, school, cls.untis_user, password)
        try:
            this_raw, next_raw = await asyncio.gather(
                untis_get_timetable(sess, base, school,
                                    this_monday, this_monday + timedelta(days=4)),
                untis_get_timetable(sess, base, school,
                                    next_monday, next_monday + timedelta(days=4)),
            )
        finally:
            await untis_logout(sess, base, school)

        if not this_raw and not next_raw:
            return {"configured": True, "error":
                f"Keine Stunden zurückgegeben (personId={sess.person_id}, personType={sess.person_type}). "
                f"Rohantwort leer – bitte prüfen ob der Untis-Account Schüler-Typ hat."}

        return {
            "configured": True,
            "this_week": {"start": this_monday.isoformat(), "lessons": parse_lessons(this_raw)},
            "next_week": {"start": next_monday.isoformat(), "lessons": parse_lessons(next_raw)},
        }
    except ValueError as e:
        return {"configured": True, "error": str(e)}
    except Exception as e:
        return {"configured": True, "error": str(e)}
