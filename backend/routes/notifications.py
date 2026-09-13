from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.user import User
from backend.models.notification_setting import (
    UserNotificationSetting, DEFAULT_HW_REMINDERS, DEFAULT_EVENT_REMINDERS
)
from backend.schemas import NotificationSettingOut, NotificationSettingUpdate
import json
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

def _to_out(ns: UserNotificationSetting) -> NotificationSettingOut:
    try:
        hw_rem = json.loads(ns.homework_reminders)
    except Exception:
        hw_rem = json.loads(DEFAULT_HW_REMINDERS)

    try:
        ev_rem = json.loads(ns.event_reminders)
    except Exception:
        ev_rem = json.loads(DEFAULT_EVENT_REMINDERS)

    return NotificationSettingOut(
        user_id=ns.user_id,
        enabled=ns.enabled,
        homework_new=ns.homework_new,
        homework_reminders=hw_rem,
        homework_daily_reminder=ns.homework_daily_reminder,
        homework_daily_time=ns.homework_daily_time,
        event_new=ns.event_new,
        event_reminders=ev_rem,
        meal_reminder_mode=ns.meal_reminder_mode,
        meal_reminder_time=ns.meal_reminder_time,
        timetable_changes=ns.timetable_changes,
        timetable_before_first_lesson=ns.timetable_before_first_lesson,
        timetable_first_lesson_lead_minutes=ns.timetable_first_lesson_lead_minutes,
        timetable_before_lesson_end=ns.timetable_before_lesson_end,
        timetable_lesson_end_lead_minutes=ns.timetable_lesson_end_lead_minutes,
        timetable_before_break=ns.timetable_before_break,
        timetable_break_lead_minutes=ns.timetable_break_lead_minutes,
        timetable_before_break_end=ns.timetable_before_break_end,
        timetable_break_end_lead_minutes=ns.timetable_break_end_lead_minutes,
        timetable_end_of_day_summary=ns.timetable_end_of_day_summary,
        timetable_end_of_day_delay_minutes=ns.timetable_end_of_day_delay_minutes,
    )

@router.get("/settings", response_model=NotificationSettingOut)
async def get_settings(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = await db.execute(select(UserNotificationSetting).where(UserNotificationSetting.user_id == current_user.id))
    ns = res.scalar_one_or_none()
    if not ns:
        ns = UserNotificationSetting(user_id=current_user.id)
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
    return _to_out(ns)

@router.put("/settings", response_model=NotificationSettingOut)
async def update_settings(data: NotificationSettingUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = await db.execute(select(UserNotificationSetting).where(UserNotificationSetting.user_id == current_user.id))
    ns = res.scalar_one_or_none()
    if not ns:
        ns = UserNotificationSetting(user_id=current_user.id)
        db.add(ns)

    if data.enabled is not None:
        ns.enabled = data.enabled
    if data.homework_new is not None:
        ns.homework_new = data.homework_new
    if data.homework_reminders is not None:
        ns.homework_reminders = json.dumps([r.model_dump() for r in data.homework_reminders])
    if data.homework_daily_reminder is not None:
        ns.homework_daily_reminder = data.homework_daily_reminder
    if data.homework_daily_time is not None:
        ns.homework_daily_time = data.homework_daily_time
    if data.event_new is not None:
        ns.event_new = data.event_new
    if data.event_reminders is not None:
        ns.event_reminders = json.dumps([r.model_dump() for r in data.event_reminders])
    if data.meal_reminder_mode is not None:
        ns.meal_reminder_mode = data.meal_reminder_mode
    if data.meal_reminder_time is not None:
        ns.meal_reminder_time = data.meal_reminder_time
    if data.timetable_changes is not None:
        ns.timetable_changes = data.timetable_changes
    if data.timetable_before_first_lesson is not None:
        ns.timetable_before_first_lesson = data.timetable_before_first_lesson
    if data.timetable_first_lesson_lead_minutes is not None:
        ns.timetable_first_lesson_lead_minutes = data.timetable_first_lesson_lead_minutes
    if data.timetable_before_lesson_end is not None:
        ns.timetable_before_lesson_end = data.timetable_before_lesson_end
    if data.timetable_lesson_end_lead_minutes is not None:
        ns.timetable_lesson_end_lead_minutes = data.timetable_lesson_end_lead_minutes
    if data.timetable_before_break is not None:
        ns.timetable_before_break = data.timetable_before_break
    if data.timetable_break_lead_minutes is not None:
        ns.timetable_break_lead_minutes = data.timetable_break_lead_minutes
    if data.timetable_before_break_end is not None:
        ns.timetable_before_break_end = data.timetable_before_break_end
    if data.timetable_break_end_lead_minutes is not None:
        ns.timetable_break_end_lead_minutes = data.timetable_break_end_lead_minutes
    if data.timetable_end_of_day_summary is not None:
        ns.timetable_end_of_day_summary = data.timetable_end_of_day_summary
    if data.timetable_end_of_day_delay_minutes is not None:
        ns.timetable_end_of_day_delay_minutes = data.timetable_end_of_day_delay_minutes

    await db.commit()
    await db.refresh(ns)
    return _to_out(ns)
