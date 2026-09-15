from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db, AsyncSessionLocal
from backend.auth import get_current_user
from backend.models.class_group import ClassGroup
from backend.models.manual_timetable_entry import ManualTimetableEntry
from backend.models.subject import Subject
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
    room          = rooms[0]    if rooms    else ""
    teacher       = teachers[0] if teachers else ""

    # For an irregular (substituted) period, WebUntis can tell us what the
    # room/teacher originally were before the change — a cancelled period
    # has no "instead", so this only makes sense to look at for "irregular".
    # original_rooms/original_teachers throw KeyError when the raw data has
    # no 'orgid' (i.e. nothing actually changed on that field specifically),
    # same defensive pattern the library's own properties use internally.
    original_room = None
    original_teacher = None
    if code == "irregular":
        try:
            orig_rooms = [r.name for r in p.original_rooms]
            if orig_rooms and orig_rooms[0] != room:
                original_room = orig_rooms[0]
        except Exception:
            pass
        try:
            orig_teachers = [t.name for t in p.original_teachers]
            if orig_teachers and orig_teachers[0] != teacher:
                original_teacher = orig_teachers[0]
        except Exception:
            pass

    # WebUntis's own human-readable substitution note (e.g. "Vertreten
    # durch Hr. Müller", "Raumtausch mit 9b") — only populated when fetched
    # via my_timetable()/timetable_extended() with showSubstText=True,
    # which is what _fetch_two_weeks()/_fetch_range() actually call.
    try:
        subst_text = (getattr(p, "substText", "") or "").strip()
    except Exception:
        subst_text = ""

    return {
        "date":              p.start.strftime("%Y%m%d"),
        "startTime":         int(p.start.strftime("%H%M")),
        "endTime":           int(p.end.strftime("%H%M")),
        "subject":           long_subjects[0] if long_subjects else (subjects[0] if subjects else ""),
        "subject_short":     subjects[0] if subjects else "",
        "teacher":           teacher,
        "room":              room,
        "cancelled":         code == "cancelled",
        "substituted":       code == "irregular",
        "original_room":     original_room,
        "original_teacher":  original_teacher,
        "subst_text":        subst_text or None,
    }

def _fetch_holidays(sess) -> list:
    """Fetches the school's official holiday periods (e.g. "Ferien") so they
    can be shown on the timetable the same way Untis itself shows them —
    Session.holidays() returns every holiday the school has configured, not
    scoped to a date range, so the frontend filters to what's relevant."""
    try:
        return [
            {
                "start": h.start.strftime("%Y%m%d"),
                "end": h.end.strftime("%Y%m%d"),
                "name": h.name,
                "short_name": h.short_name,
            }
            for h in sess.holidays()
        ]
    except Exception:
        return []

def _fetch_two_weeks(server: str, school: str, username: str, password: str,
                     class_name: str, this_monday: date) -> tuple[list, list, list]:
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
            periods = list(sess.timetable_extended(klasse=matched[0], start=start, end=end))
            return sorted([_period_to_dict(p) for p in periods],
                          key=lambda x: (x["date"], x["startTime"]))

        this_lessons = fetch_week(this_monday, this_monday + timedelta(days=4))
        next_lessons = fetch_week(next_monday, next_monday + timedelta(days=4))
        holidays = _fetch_holidays(sess)
        return this_lessons, next_lessons, holidays
    finally:
        try:
            sess.logout()
        except Exception:
            pass

def _fetch_range(server: str, school: str, username: str, password: str,
                  class_name: str, start: date, end: date) -> list:
    """Single-session fetch of every lesson across an arbitrary [start, end]
    range, as one flat list rather than a this-week/next-week split — used
    where a caller just wants "everything taught in this window" (e.g.
    discovering which subjects exist over several weeks) rather than the
    day/week-view breakdown _fetch_two_weeks() is shaped for."""
    sess = webuntis.Session(
        server=server, username=username, password=password,
        school=school, useragent="SofiaApp/1.0",
    )
    sess.login()
    try:
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
    finally:
        try:
            sess.logout()
        except Exception:
            pass

from backend.services.timetable_cache import get_cached_timetable, set_cached_timetable

async def _manual_week(db: AsyncSession, class_id: int, monday: date) -> dict:
    """Projects a class's recurring weekly manual/photo timetable onto one
    real week's dates, in the exact same lesson-dict shape Untis produces —
    everything downstream (day/week views, homework's current-subject
    lookup) reads that shape and doesn't care which source it came from."""
    result = await db.execute(
        select(ManualTimetableEntry, Subject)
        .join(Subject, ManualTimetableEntry.subject_id == Subject.id)
        .where(ManualTimetableEntry.class_id == class_id)
    )
    lessons = []
    for entry, subject in result.all():
        d = monday + timedelta(days=entry.weekday)
        lessons.append({
            "date":          d.strftime("%Y%m%d"),
            "startTime":     entry.start_time,
            "endTime":       entry.end_time,
            "subject":       subject.name,
            "subject_short": subject.short_name or subject.name,
            "teacher":       "",
            "room":          entry.room or "",
            "cancelled":     False,
            "substituted":   False,
        })
    lessons.sort(key=lambda x: (x["date"], x["startTime"]))
    return {"start": monday.isoformat(), "lessons": lessons}

async def _get_manual_timetable(db: AsyncSession, class_id: int) -> dict:
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    next_monday = this_monday + timedelta(days=7)
    this_week = await _manual_week(db, class_id, this_monday)
    next_week = await _manual_week(db, class_id, next_monday)
    if not this_week["lessons"] and not next_week["lessons"]:
        return {"configured": False}
    return {"configured": True, "this_week": this_week, "next_week": next_week}

@router.get("/")
async def get_timetable(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        raise HTTPException(400, "No class assigned")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == current_user.class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        return {"configured": False}

    if cls.timetable_source == "manual":
        return await _get_manual_timetable(db, cls.id)

    if not cls.untis_url:
        return {"configured": False}

    # 1. Return from 5-minute cache if available
    cached = get_cached_timetable(cls.id)
    if cached and not cached.get("error"):
        return {
            "configured": True,
            "this_week": cached["this_week"],
            "next_week": cached["next_week"],
            "holidays": cached.get("holidays", []),
        }

    try:
        password = decrypt_password(cls.untis_password_enc) if cls.untis_password_enc else ""
        server   = _strip_server(cls.untis_url)
        today    = date.today()
        this_monday = today - timedelta(days=today.weekday())
        next_monday = this_monday + timedelta(days=7)

        loop = asyncio.get_event_loop()
        this_lessons, next_lessons, holidays = await loop.run_in_executor(
            None, _fetch_two_weeks, server, cls.untis_school,
            cls.untis_user, password, cls.untis_class or "", this_monday,
        )

        this_week = {"start": this_monday.isoformat(), "lessons": this_lessons}
        next_week = {"start": next_monday.isoformat(), "lessons": next_lessons}
        set_cached_timetable(cls.id, this_week, next_week, holidays)

        return {
            "configured": True,
            "this_week": this_week,
            "next_week": next_week,
            "holidays": holidays,
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
        this_lessons, _, _ = await loop.run_in_executor(
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
                result = await db.execute(select(ClassGroup).where(
                    ClassGroup.untis_url.isnot(None), ClassGroup.timetable_source == "untis"
                ))
                for cls in result.scalars().all():
                    await _check_class_cancellations(db, cls)
        except Exception as e:
            logger.warning("Cancelled-lesson poll loop error: %s", e)
        await asyncio.sleep(900)
