from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.config import settings
from cryptography.fernet import Fernet
from datetime import date, timedelta
import asyncio
import requests
from urllib3.exceptions import InsecureRequestWarning
import webuntis

# Disable SSL verification globally for webuntis (uses requests internally)
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
_original_send = requests.Session.send
def _patched_send(self, request, **kwargs):
    kwargs['verify'] = False
    return _original_send(self, request, **kwargs)
requests.Session.send = _patched_send

router = APIRouter(prefix="/api/v1/timetable", tags=["timetable"])

def decrypt_password(enc: str) -> str:
    key = settings.encryption_key
    if not key:
        return enc
    return Fernet(key.encode()).decrypt(enc.encode()).decode()

def _strip_server(url: str) -> str:
    if "://" in url:
        url = url.split("://")[1]
    if "/" in url:
        url = url.split("/")[0]
    return url

def _fetch_timetable(server: str, school: str, username: str, password: str,
                     class_name: str, monday: date, friday: date) -> list:
    """Runs synchronously — call via run_in_executor."""
    session = webuntis.Session(
        server=server, username=username, password=password,
        school=school, useragent="SofiaApp/1.0"
    )
    session.login()
    try:
        # Strategy 1: own timetable
        try:
            periods = list(session.my_timetable(start=monday, end=friday))
        except Exception:
            periods = None

        # Strategy 2: via class
        if not periods:
            klassen = list(session.klassen())
            matched = [k for k in klassen if k.name.lower() == class_name.lower()]
            if not matched and klassen:
                matched = [klassen[0]]
            if not matched:
                raise Exception("Keine Klasse gefunden")
            periods = list(session.timetable(klasse=matched[0], start=monday, end=friday))

        result = []
        for p in periods:
            subjects = [s.name for s in p.subjects] if p.subjects else []
            long_subjects = [getattr(s, 'long_name', s.name) for s in p.subjects] if p.subjects else []
            teachers = [t.name for t in p.teachers] if p.teachers else []
            rooms = [r.name for r in p.rooms] if p.rooms else []
            code = getattr(p, 'code', None)
            result.append({
                "date": p.start.strftime("%Y%m%d"),
                "startTime": int(p.start.strftime("%H%M")),
                "endTime": int(p.end.strftime("%H%M")),
                "subject": long_subjects[0] if long_subjects else (subjects[0] if subjects else ""),
                "subject_short": subjects[0] if subjects else "",
                "teacher": teachers[0] if teachers else "",
                "room": rooms[0] if rooms else "",
                "cancelled": code == "cancelled",
                "substituted": code == "irregular",
            })
        return sorted(result, key=lambda x: (x["date"], x["startTime"]))
    finally:
        try:
            session.logout()
        except Exception:
            pass

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
        server = _strip_server(cls.untis_url)
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        next_monday = this_monday + timedelta(days=7)

        loop = asyncio.get_event_loop()

        this_lessons, next_lessons = await asyncio.gather(
            loop.run_in_executor(None, _fetch_timetable, server, cls.untis_school,
                                 cls.untis_user, password, cls.untis_class or "",
                                 this_monday, this_monday + timedelta(days=4)),
            loop.run_in_executor(None, _fetch_timetable, server, cls.untis_school,
                                 cls.untis_user, password, cls.untis_class or "",
                                 next_monday, next_monday + timedelta(days=4)),
        )

        return {
            "configured": True,
            "this_week": {"start": this_monday.isoformat(), "lessons": this_lessons},
            "next_week": {"start": next_monday.isoformat(), "lessons": next_lessons},
        }
    except Exception as e:
        return {"configured": True, "error": str(e)}
