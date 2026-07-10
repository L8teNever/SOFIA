from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db, AsyncSessionLocal
from backend.auth import get_current_user
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.config import settings
from backend.routes.vapid import push_to_users
from cryptography.fernet import Fernet
from datetime import date, datetime, timedelta
import asyncio
import logging
import requests
import webuntis
from urllib3.exceptions import InsecureRequestWarning

logger = logging.getLogger(__name__)

# Disable SSL verification for webuntis (uses requests internally)
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
_orig_send = requests.Session.send
def _patched_send(self, request, **kwargs):
    kwargs['verify'] = False
    return _orig_send(self, request, **kwargs)
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

def _period_to_dict(p) -> dict:
    subjects      = [s.name for s in p.subjects]  if p.subjects  else []
    long_subjects = [(getattr(s, 'long_name', None) or s.name) for s in p.subjects] if p.subjects else []
    teachers      = [t.name for t in p.teachers]  if p.teachers  else []
    rooms         = [r.name for r in p.rooms]      if p.rooms     else []
    code          = getattr(p, 'code', None)
    return {
        "date":          p.start.strftime("%Y%m%d"),
        "startTime":     int(p.start.strftime("%H%M")),
        "endTime":       int(p.end.strftime("%H%M")),
        "subject":       long_subjects[0] if long_subjects else (subjects[0] if subjects else ""),
        "subject_short": subjects[0] if subjects else "",
        "teacher":       teachers[0] if teachers else "",
        "room":          rooms[0]    if rooms    else "",
        "cancelled":     code == "cancelled",
        "substituted":   code == "irregular",
    }

def _fetch_two_weeks(server: str, school: str, username: str, password: str,
                     class_name: str, this_monday: date) -> tuple[list, list]:
    """Fetches both weeks in a single session to avoid concurrent login issues."""
    next_monday = this_monday + timedelta(days=7)

    sess = webuntis.Session(
        server=server, username=username, password=password,
        school=school, useragent="SofiaApp/1.0",
    )
    sess.login()
    try:
        def fetch_week(start: date, end: date) -> list:
            try:
                periods = list(sess.my_timetable(start=start, end=end))
                if periods:
                    return sorted([_period_to_dict(p) for p in periods],
                                   key=lambda x: (x["date"], x["startTime"]))
            except Exception:
                pass
            klassen = list(sess.klassen())
            if not klassen:
                return []
            matched = [k for k in klassen if k.name.lower() == class_name.lower()]
            if not matched:
                matched = [klassen[0]]
            periods = list(sess.timetable(klasse=matched[0], start=start, end=end))
            return sorted([_period_to_dict(p) for p in periods],
                          key=lambda x: (x["date"], x["startTime"]))

        this_lessons = fetch_week(this_monday, this_monday + timedelta(days=4))
        next_lessons = fetch_week(next_monday, next_monday + timedelta(days=4))
        return this_lessons, next_lessons
    finally:
        try:
            sess.logout()
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
        server   = _strip_server(cls.untis_url)
        today    = date.today()
        this_monday = today - timedelta(days=today.weekday())
        next_monday = this_monday + timedelta(days=7)

        loop = asyncio.get_event_loop()
        this_lessons, next_lessons = await loop.run_in_executor(
            None, _fetch_two_weeks, server, cls.untis_school,
            cls.untis_user, password, cls.untis_class or "", this_monday,
        )

        return {
            "configured": True,
            "this_week": {"start": this_monday.isoformat(), "lessons": this_lessons},
            "next_week": {"start": next_monday.isoformat(), "lessons": next_lessons},
        }
    except Exception as e:
        return {"configured": True, "error": str(e)}


# In-memory: class_id -> set of (date, startTime, subject_short) keys that were
# cancelled as of the last poll. Used to detect newly-cancelled lessons so we
# only notify once per cancellation, not on every poll.
_cancelled_state: dict[int, set] = {}

async def _check_class_cancellations(db: AsyncSession, cls: ClassGroup):
    try:
        password = decrypt_password(cls.untis_password_enc) if cls.untis_password_enc else ""
        server = _strip_server(cls.untis_url)
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        loop = asyncio.get_event_loop()
        this_lessons, _ = await loop.run_in_executor(
            None, _fetch_two_weeks, server, cls.untis_school,
            cls.untis_user, password, cls.untis_class or "", this_monday,
        )
    except Exception as e:
        logger.warning("Cancelled-lesson poll failed for class %s: %s", cls.id, e)
        return

    today_str = today.strftime("%Y%m%d")
    now_hhmm = int(datetime.now().strftime("%H%M"))
    upcoming_cancelled = {
        (l["date"], l["startTime"], l["subject_short"])
        for l in this_lessons
        if l["cancelled"] and (l["date"] > today_str or (l["date"] == today_str and l["startTime"] >= now_hhmm))
    }

    previous = _cancelled_state.get(cls.id)
    _cancelled_state[cls.id] = upcoming_cancelled
    if previous is None:
        return  # first poll for this class — just establish a baseline, don't spam old cancellations

    new_cancellations = upcoming_cancelled - previous
    if not new_cancellations:
        return

    result = await db.execute(select(User).where(User.class_id == cls.id))
    members = list(result.scalars().all())
    if not members:
        return

    for lesson_date, start_time, subject in sorted(new_cancellations):
        d = datetime.strptime(lesson_date, "%Y%m%d")
        time_str = f"{start_time // 100:02d}:{start_time % 100:02d}"
        title = f"{subject or 'Stunde'} fällt aus"
        body = f"{d.strftime('%d.%m.')} um {time_str} Uhr"
        await push_to_users(db, members, title, body)

async def poll_cancelled_lessons_loop():
    """Background loop: periodically re-fetches each configured class's
    timetable and pushes a notification when a lesson newly turns up as
    cancelled. Runs for the app's whole lifetime (started in main.py)."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(ClassGroup).where(ClassGroup.untis_url.isnot(None)))
                for cls in result.scalars().all():
                    await _check_class_cancellations(db, cls)
        except Exception as e:
            logger.warning("Cancelled-lesson poll loop error: %s", e)
        await asyncio.sleep(900)
