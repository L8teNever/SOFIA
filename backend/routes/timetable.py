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

def parse_untis_week(resp_json: dict) -> list:
    try:
        data = resp_json.get("data", {}).get("result", {}).get("data", {})
        el_map = {}
        for el in data.get("elements", []):
            el_map[(el["type"], el["id"])] = el
        lessons = []
        for _, periods in data.get("elementPeriods", {}).items():
            for p in periods:
                subject = teacher = room = ""
                for el in p.get("elements", []):
                    entry = el_map.get((el["type"], el["id"]), {})
                    if el["type"] == 3:
                        subject = entry.get("name", "")
                    elif el["type"] == 2:
                        teacher = entry.get("name", "")
                    elif el["type"] == 4:
                        room = entry.get("name", "")
                is_info = p.get("is", {})
                lessons.append({
                    "date": str(p.get("date", "")),
                    "startTime": p.get("startTime", 0),
                    "endTime": p.get("endTime", 0),
                    "subject": subject or p.get("lessonText", ""),
                    "teacher": teacher,
                    "room": room,
                    "cancelled": is_info.get("cancelled", False),
                    "substituted": is_info.get("substituted", False),
                })
        return sorted(lessons, key=lambda x: (x["date"], x["startTime"]))
    except Exception:
        return []

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

            def tt_url(monday: date) -> str:
                return (f"{base}/WebUntis/api/public/timetable/weekly/data"
                        f"?elementType=1&elementId=0&date={monday.isoformat()}&formatId=1")

            this_resp, next_resp = await asyncio.gather(
                client.get(tt_url(this_monday), cookies=cookies, headers={"school": school}),
                client.get(tt_url(next_monday), cookies=cookies, headers={"school": school}),
            )

            await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "2", "method": "logout", "params": {}, "jsonrpc": "2.0"},
                cookies=cookies,
            )

        this_lessons = parse_untis_week(this_resp.json()) if this_resp.status_code == 200 else []
        next_lessons = parse_untis_week(next_resp.json()) if next_resp.status_code == 200 else []

        return {
            "configured": True,
            "this_week": {"start": this_monday.isoformat(), "lessons": this_lessons},
            "next_week": {"start": next_monday.isoformat(), "lessons": next_lessons},
        }
    except Exception as e:
        return {"configured": True, "error": str(e)}
