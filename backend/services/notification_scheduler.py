import asyncio
import json
import logging
from datetime import datetime, date, timedelta
from typing import List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from backend.database import AsyncSessionLocal
from backend.models.user import User
from backend.models.class_group import ClassGroup
from backend.models.homework import Homework
from backend.models.calendar_event import CalendarEvent
from backend.models.meal_plan import MealPlanDay
from backend.models.subject import Subject
from backend.models.notification_setting import (
    UserNotificationSetting, DEFAULT_HW_REMINDERS, DEFAULT_EVENT_REMINDERS
)
from backend.models.sent_notification_log import SentNotificationLog
from backend.routes.vapid import push_to_users
from backend.services.timetable_cache import (
    get_cached_timetable, set_cached_timetable, peek_cached_lessons, detect_timetable_changes
)

logger = logging.getLogger(__name__)

# In-memory deduplication set for fast lookup
_sent_keys_cache: Set[str] = set()

async def is_sent(db: AsyncSession, key: str) -> bool:
    if key in _sent_keys_cache:
        return True
    res = await db.execute(select(SentNotificationLog).where(SentNotificationLog.notification_key == key))
    if res.scalar_one_or_none():
        _sent_keys_cache.add(key)
        return True
    return False

async def mark_sent(db: AsyncSession, key: str):
    _sent_keys_cache.add(key)
    log_entry = SentNotificationLog(notification_key=key)
    db.add(log_entry)
    try:
        await db.commit()
    except Exception:
        await db.rollback()

async def get_user_settings(db: AsyncSession, user_id: int) -> UserNotificationSetting:
    res = await db.execute(select(UserNotificationSetting).where(UserNotificationSetting.user_id == user_id))
    ns = res.scalar_one_or_none()
    if not ns:
        ns = UserNotificationSetting(user_id=user_id)
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
    return ns

# ---------------------------------------------------------------------------
# Direct Creation Notifications
# ---------------------------------------------------------------------------

async def notify_new_homework(db: AsyncSession, hw: Homework, creator: User):
    """Sends notification to all class members who have homework_new enabled."""
    if not hw.class_id:
        return

    # Find class members (excluding creator)
    users_res = await db.execute(select(User).where(User.class_id == hw.class_id, User.id != creator.id))
    members = list(users_res.scalars().all())
    if not members:
        return

    # Get subject name
    sub_res = await db.execute(select(Subject).where(Subject.id == hw.subject_id))
    sub = sub_res.scalar_one_or_none()
    sub_name = (sub.name or sub.short_name) if sub else "Hausaufgabe"

    target_users = []
    for u in members:
        ns = await get_user_settings(db, u.id)
        if ns.enabled and ns.homework_new:
            target_users.append(u)

    if target_users:
        date_formatted = hw.due_date
        try:
            d = datetime.strptime(hw.due_date, "%Y-%m-%d")
            date_formatted = d.strftime("%d.%m.")
        except Exception:
            pass

        title = f"📚 Neue Hausaufgabe in {sub_name}"
        body = f"{hw.description[:90]} (Fällig am {date_formatted})"
        await push_to_users(db, target_users, title, body)

async def notify_new_event(db: AsyncSession, ev: CalendarEvent, creator: User):
    """Sends notification to all class members who have event_new enabled."""
    if not ev.class_id or str(ev.event_type) == "personal" or getattr(ev.event_type, "value", str(ev.event_type)) == "personal":
        return

    users_res = await db.execute(select(User).where(User.class_id == ev.class_id, User.id != creator.id))

    members = list(users_res.scalars().all())
    if not members:
        return

    target_users = []
    for u in members:
        ns = await get_user_settings(db, u.id)
        if ns.enabled and ns.event_new:
            target_users.append(u)

    if target_users:
        date_formatted = ev.date
        try:
            d = datetime.strptime(ev.date, "%Y-%m-%d")
            date_formatted = d.strftime("%d.%m.")
        except Exception:
            pass

        time_part = f" um {ev.time} Uhr" if ev.time else ""
        title = f"📅 Neuer Termin: {ev.title}"
        body = f"Am {date_formatted}{time_part}"
        await push_to_users(db, target_users, title, body)

# ---------------------------------------------------------------------------
# Background Timetable & Reminders Loops
# ---------------------------------------------------------------------------

async def _poll_class_timetable(db: AsyncSession, cls: ClassGroup):
    from backend.routes.timetable import decrypt_password, _strip_server, _fetch_two_weeks
    try:
        password = decrypt_password(cls.untis_password_enc) if cls.untis_password_enc else ""
        server = _strip_server(cls.untis_url)
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        loop = asyncio.get_event_loop()
        this_lessons, next_lessons, holidays = await loop.run_in_executor(
            None, _fetch_two_weeks, server, cls.untis_school,
            cls.untis_user, password, cls.untis_class or "", this_monday,
        )
    except Exception as e:
        logger.warning("Untis poll failed for class %s: %s", cls.id, e)
        return

    old_lessons = peek_cached_lessons(cls.id)
    this_week = {"start": this_monday.isoformat(), "lessons": this_lessons}
    next_week = {"start": (this_monday + timedelta(days=7)).isoformat(), "lessons": next_lessons}
    set_cached_timetable(cls.id, this_week, next_week, holidays)

    if not old_lessons:
        # First poll establishes baseline
        return

    all_new_lessons = (this_lessons or []) + (next_lessons or [])
    changes = detect_timetable_changes(old_lessons, all_new_lessons)
    if not changes:
        return

    users_res = await db.execute(select(User).where(User.class_id == cls.id))
    members = list(users_res.scalars().all())
    if not members:
        return

    for change in changes:
        key = f"tt_change_{cls.id}_{change['key']}"
        if await is_sent(db, key):
            continue

        target_users = []
        for u in members:
            ns = await get_user_settings(db, u.id)
            if ns.enabled and ns.timetable_changes:
                target_users.append(u)

        if target_users:
            await push_to_users(db, target_users, change["title"], change["body"])
        await mark_sent(db, key)

async def _poll_stundenplan_triggers(db: AsyncSession, now: datetime):
    """Handles:
    - Vor der 1. Stunde
    - Vor Stundenende
    - Vor Pausenbeginn
    - Vor Pausenende / nächster Stunde
    - Schulschluss-Zusammenfassung
    """
    today_str = now.strftime("%Y%m%d")
    today_iso = now.strftime("%Y-%m-%d")
    now_hhmm = int(now.strftime("%H%M"))

    classes_res = await db.execute(select(ClassGroup).where(ClassGroup.untis_url.isnot(None)))
    classes = list(classes_res.scalars().all())

    for cls in classes:
        lessons = peek_cached_lessons(cls.id)
        today_lessons = [l for l in lessons if l["date"] == today_str and not l.get("cancelled")]
        if not today_lessons:
            continue

        today_lessons.sort(key=lambda x: x["startTime"])

        users_res = await db.execute(select(User).where(User.class_id == cls.id))
        members = list(users_res.scalars().all())
        if not members:
            continue

        # 1. Erste Stunde
        first_lesson = today_lessons[0]
        fl_start_h = first_lesson["startTime"] // 100
        fl_start_m = first_lesson["startTime"] % 100
        fl_dt = now.replace(hour=fl_start_h, minute=fl_start_m, second=0, microsecond=0)
        minutes_to_first = (fl_dt - now).total_seconds() / 60.0

        for u in members:
            ns = await get_user_settings(db, u.id)
            if not ns.enabled:
                continue

            lead = ns.timetable_first_lesson_lead_minutes
            if ns.timetable_before_first_lesson and 0 <= minutes_to_first <= lead:
                key = f"tt_first_{u.id}_{today_str}"
                if not await is_sent(db, key):
                    subj = first_lesson.get("subject") or first_lesson.get("subject_short") or "Unterricht"
                    r_str = f" in Raum {first_lesson['room']}" if first_lesson.get("room") else ""
                    time_fmt = f"{fl_start_h:02d}:{fl_start_m:02d}"
                    title = f"🔔 Erste Stunde in {int(minutes_to_first + 0.5)} Min: {subj}"
                    body = f"Beginnt um {time_fmt} Uhr{r_str}."
                    await push_to_users(db, [u], title, body)
                    await mark_sent(db, key)

        # 2. Unterrichtsende / Pause / Pausenende
        for i, l in enumerate(today_lessons):
            l_start_h = l["startTime"] // 100
            l_start_m = l["startTime"] % 100
            l_end_h   = l["endTime"] // 100
            l_end_m   = l["endTime"] % 100
            l_end_dt   = now.replace(hour=l_end_h, minute=l_end_m, second=0, microsecond=0)
            l_start_dt = now.replace(hour=l_start_h, minute=l_start_m, second=0, microsecond=0)

            mins_to_end = (l_end_dt - now).total_seconds() / 60.0
            next_lesson = today_lessons[i+1] if i+1 < len(today_lessons) else None

            # Vor Stundenende
            if 0 <= mins_to_end <= 15 and now_hhmm >= l["startTime"]:
                for u in members:
                    ns = await get_user_settings(db, u.id)
                    if not ns.enabled or not ns.timetable_before_lesson_end:
                        continue
                    if 0 <= mins_to_end <= ns.timetable_lesson_end_lead_minutes:
                        key = f"tt_end_{u.id}_{today_str}_{l['startTime']}"
                        if not await is_sent(db, key):
                            subj = l.get("subject") or l.get("subject_short") or "Unterricht"
                            title = f"⏱️ Noch {int(mins_to_end + 0.5)} Minuten Unterricht"
                            body = f"{subj} endet um {l_end_h:02d}:{l_end_m:02d} Uhr."
                            await push_to_users(db, [u], title, body)
                            await mark_sent(db, key)

            # Pause beginnt (Pause zwischen Stunden > 5 min)
            if next_lesson:
                nl_start_h = next_lesson["startTime"] // 100
                nl_start_m = next_lesson["startTime"] % 100
                nl_start_dt = now.replace(hour=nl_start_h, minute=nl_start_m, second=0, microsecond=0)
                gap_mins = (nl_start_dt - l_end_dt).total_seconds() / 60.0

                if gap_mins >= 8:
                    # Vor Pausenbeginn
                    if 0 <= mins_to_end <= 15 and now_hhmm >= l["startTime"]:
                        for u in members:
                            ns = await get_user_settings(db, u.id)
                            if not ns.enabled or not ns.timetable_before_break:
                                continue
                            if 0 <= mins_to_end <= ns.timetable_break_lead_minutes:
                                key = f"tt_brk_start_{u.id}_{today_str}_{l['startTime']}"
                                if not await is_sent(db, key):
                                    title = f"☕ Pause in {int(mins_to_end + 0.5)} Minuten"
                                    body = f"Gleich hast du Pause ({int(gap_mins)} Min bis zur nächsten Stunde)."
                                    await push_to_users(db, [u], title, body)
                                    await mark_sent(db, key)

                    # Vor Pausenende / nächste Stunde
                    mins_to_next = (nl_start_dt - now).total_seconds() / 60.0
                    if 0 <= mins_to_next <= 15 and now >= l_end_dt:
                        for u in members:
                            ns = await get_user_settings(db, u.id)
                            if not ns.enabled or not ns.timetable_before_break_end:
                                continue
                            if 0 <= mins_to_next <= ns.timetable_break_end_lead_minutes:
                                key = f"tt_brk_end_{u.id}_{today_str}_{next_lesson['startTime']}"
                                if not await is_sent(db, key):
                                    n_subj = next_lesson.get("subject") or next_lesson.get("subject_short") or "Unterricht"
                                    n_room = f" (Raum {next_lesson['room']})" if next_lesson.get("room") else ""
                                    title = f"🔔 Pause endet gleich: {n_subj}"
                                    body = f"Nächste Stunde beginnt in {int(mins_to_next + 0.5)} Min{n_room}."
                                    await push_to_users(db, [u], title, body)
                                    await mark_sent(db, key)

        # 3. Schulschluss-Zusammenfassung
        last_lesson = today_lessons[-1]
        ll_end_h = last_lesson["endTime"] // 100
        ll_end_m = last_lesson["endTime"] % 100
        ll_end_dt = now.replace(hour=ll_end_h, minute=ll_end_m, second=0, microsecond=0)
        mins_since_school_ended = (now - ll_end_dt).total_seconds() / 60.0

        if mins_since_school_ended >= 0:
            for u in members:
                ns = await get_user_settings(db, u.id)
                if not ns.enabled or not ns.timetable_end_of_day_summary:
                    continue

                delay = ns.timetable_end_of_day_delay_minutes
                if mins_since_school_ended >= delay and mins_since_school_ended <= delay + 60:
                    key = f"tt_summary_{u.id}_{today_str}"
                    if not await is_sent(db, key):
                        # Query user's open homework due today or upcoming
                        hw_res = await db.execute(
                            select(Homework).where(
                                Homework.class_id == cls.id,
                                Homework.due_date >= today_iso
                            )
                        )
                        all_hw = list(hw_res.scalars().all())
                        open_hw = [h for h in all_hw if u.id not in (h.checked_by or [])]

                        if not open_hw:
                            title = "🎒 Schule ist aus!"
                            body = "Du hast aktuell keine offenen Hausaufgaben 🎉"
                        else:
                            count = len(open_hw)
                            s_word = "Hausaufgabe" if count == 1 else "Hausaufgaben"
                            title = f"🎒 Schule aus: {count} offene {s_word}"
                            body = f"Du hast noch {count} {s_word} zu erledigen. Vergiss nicht nachzuschauen!"

                        await push_to_users(db, [u], title, body)
                        await mark_sent(db, key)

async def _poll_homework_reminders(db: AsyncSession, now: datetime):
    today_iso = now.strftime("%Y-%m-%d")
    now_time_str = now.strftime("%H:%M")

    # Fetch active upcoming homework
    res = await db.execute(select(Homework).where(Homework.due_date >= today_iso))
    all_hw = list(res.scalars().all())
    if not all_hw:
        return

    # Group homework by class
    hw_by_class = {}
    for h in all_hw:
        hw_by_class.setdefault(h.class_id, []).append(h)

    for class_id, hw_list in hw_by_class.items():
        users_res = await db.execute(select(User).where(User.class_id == class_id))
        members = list(users_res.scalars().all())

        for u in members:
            ns = await get_user_settings(db, u.id)
            if not ns.enabled:
                continue

            open_hw = [h for h in hw_list if u.id not in (h.checked_by or [])]

            # 1. Täglich erinnern bis erledigt
            if ns.homework_daily_reminder and now_time_str == ns.homework_daily_time:
                key = f"hw_daily_{u.id}_{today_iso}"
                if not await is_sent(db, key):
                    if open_hw:
                        c = len(open_hw)
                        s = "Hausaufgabe" if c == 1 else "Hausaufgaben"
                        title = f"📚 Tägliche Erinnerung: {c} offene {s}"
                        body = f"Du hast noch {c} {s} auf der Liste. Schau jetzt kurz rein!"
                        await push_to_users(db, [u], title, body)
                    await mark_sent(db, key)

            # 2. Mehrstufige Erinnerungen
            try:
                reminders = json.loads(ns.homework_reminders)
            except Exception:
                reminders = json.loads(DEFAULT_HW_REMINDERS)

            for h in open_hw:
                try:
                    due_d = datetime.strptime(h.due_date, "%Y-%m-%d")
                    # Assume due at 08:00 morning
                    due_dt = due_d.replace(hour=8, minute=0, second=0, microsecond=0)
                    diff_seconds = (due_dt - now).total_seconds()
                except Exception:
                    continue

                if diff_seconds < 0:
                    continue

                for r in reminders:
                    r_type = r.get("type", "days")
                    r_val = int(r.get("value", 1))
                    target_sec = r_val * 86400 if r_type == "days" else r_val * 3600

                    # Check if we are within the notification window (target_sec +/- 20 min)
                    if 0 <= diff_seconds <= target_sec and diff_seconds >= target_sec - 3600:
                        key = f"hw_rem_{h.id}_{u.id}_{r_type}_{r_val}"
                        if not await is_sent(db, key):
                            sub_res = await db.execute(select(Subject).where(Subject.id == h.subject_id))
                            sub = sub_res.scalar_one_or_none()
                            sub_name = (sub.name or sub.short_name) if sub else "Hausaufgabe"

                            lead_label = f"{r_val} Tag(en)" if r_type == "days" else f"{r_val} Stunde(n)"
                            title = f"📚 Hausaufgabe in {sub_name} fällig in {lead_label}"
                            body = f"{h.description[:80]} (Fällig am {h.due_date})"
                            await push_to_users(db, [u], title, body)
                            await mark_sent(db, key)

async def _poll_calendar_reminders(db: AsyncSession, now: datetime):
    today_iso = now.strftime("%Y-%m-%d")

    res = await db.execute(select(CalendarEvent).where(CalendarEvent.date >= today_iso))
    events = list(res.scalars().all())
    if not events:
        return

    ev_by_class = {}
    for ev in events:
        ev_by_class.setdefault(ev.class_id, []).append(ev)

    for class_id, ev_list in ev_by_class.items():
        users_res = await db.execute(select(User).where(User.class_id == class_id))
        members = list(users_res.scalars().all())

        for u in members:
            ns = await get_user_settings(db, u.id)
            if not ns.enabled:
                continue

            try:
                reminders = json.loads(ns.event_reminders)
            except Exception:
                reminders = json.loads(DEFAULT_EVENT_REMINDERS)

            for ev in ev_list:
                try:
                    ev_d = datetime.strptime(ev.date, "%Y-%m-%d")
                    t_parts = (ev.time or "08:00").split(":")
                    ev_dt = ev_d.replace(hour=int(t_parts[0]), minute=int(t_parts[1]), second=0, microsecond=0)
                    diff_seconds = (ev_dt - now).total_seconds()
                except Exception:
                    continue

                if diff_seconds < 0:
                    continue

                for r in reminders:
                    r_type = r.get("type", "days")
                    r_val = int(r.get("value", 1))
                    target_sec = r_val * 86400 if r_type == "days" else r_val * 3600

                    if 0 <= diff_seconds <= target_sec and diff_seconds >= target_sec - 3600:
                        key = f"ev_rem_{ev.id}_{u.id}_{r_type}_{r_val}"
                        if not await is_sent(db, key):
                            lead_label = f"{r_val} Tag(en)" if r_type == "days" else f"{r_val} Stunde(n)"
                            title = f"📅 Termin in {lead_label}: {ev.title}"
                            body = f"Am {ev.date}" + (f" um {ev.time} Uhr" if ev.time else "")
                            await push_to_users(db, [u], title, body)
                            await mark_sent(db, key)

async def _poll_meal_reminders(db: AsyncSession, now: datetime):
    today_iso = now.strftime("%Y-%m-%d")
    tomorrow_iso = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    now_time_str = now.strftime("%H:%M")

    users_res = await db.execute(select(User))
    users = list(users_res.scalars().all())

    # Cache meals
    day_res = await db.execute(select(MealPlanDay).where(MealPlanDay.date.in_([today_iso, tomorrow_iso])))
    meal_map = {m.date: m.meal for m in day_res.scalars().all() if m.meal}

    for u in users:
        ns = await get_user_settings(db, u.id)
        if not ns.enabled or ns.meal_reminder_mode == "none":
            continue

        if now_time_str != ns.meal_reminder_time:
            continue

        if ns.meal_reminder_mode == "same_day":
            key = f"meal_today_{u.id}_{today_iso}"
            if not await is_sent(db, key):
                meal_text = meal_map.get(today_iso)
                if meal_text:
                    title = "🍴 Heute in der Mensa"
                    body = meal_text[:110]
                    await push_to_users(db, [u], title, body)
                await mark_sent(db, key)

        elif ns.meal_reminder_mode == "day_before":
            key = f"meal_tomorrow_{u.id}_{tomorrow_iso}"
            if not await is_sent(db, key):
                meal_text = meal_map.get(tomorrow_iso)
                if meal_text:
                    title = "🍴 Morgen in der Mensa"
                    body = meal_text[:110]
                    await push_to_users(db, [u], title, body)
                await mark_sent(db, key)

# ---------------------------------------------------------------------------
# Master Background Scheduler Loops
# ---------------------------------------------------------------------------

async def timetable_5min_poll_loop():
    """Polls WebUntis every 5 minutes (300s) to update cache and detect live changes."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                classes_res = await db.execute(select(ClassGroup).where(ClassGroup.untis_url.isnot(None)))
                for cls in classes_res.scalars().all():
                    await _poll_class_timetable(db, cls)
        except Exception as e:
            logger.warning("Error in timetable_5min_poll_loop: %s", e)
        await asyncio.sleep(300)

async def reminders_60s_poll_loop():
    """Runs every 60 seconds to check time-based triggers:
    stundenplan lead alerts, homework reminders, calendar reminders, meal reminders.
    """
    while True:
        try:
            now = datetime.now()
            async with AsyncSessionLocal() as db:
                await _poll_stundenplan_triggers(db, now)
                await _poll_homework_reminders(db, now)
                await _poll_calendar_reminders(db, now)
                await _poll_meal_reminders(db, now)
        except Exception as e:
            logger.warning("Error in reminders_60s_poll_loop: %s", e)
        await asyncio.sleep(60)

async def weekly_mealplan_cleanup_loop():
    """Runs once every 6 hours to clean up old meal plan images from past weeks."""
    while True:
        try:
            from backend.routes.mealplan import cleanup_past_mealplans
            async with AsyncSessionLocal() as db:
                cleaned = await cleanup_past_mealplans(db)
                if cleaned.get("cleaned_files", 0) > 0:
                    logger.info("Auto-Cleanup: %d alte Mensa-Bilder gelöscht (%d Bytes freigegeben)", cleaned["cleaned_files"], cleaned["freed_bytes"])
        except Exception as e:
            logger.warning("Error in weekly_mealplan_cleanup_loop: %s", e)
        await asyncio.sleep(21600)  # Every 6 hours

async def start_notification_scheduler():
    """Starts background tasks concurrently."""
    t1 = asyncio.create_task(timetable_5min_poll_loop())
    t2 = asyncio.create_task(reminders_60s_poll_loop())
    t3 = asyncio.create_task(weekly_mealplan_cleanup_loop())
    try:
        await asyncio.gather(t1, t2, t3)
    except asyncio.CancelledError:
        t1.cancel()
        t2.cancel()
        t3.cancel()
