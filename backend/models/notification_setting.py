from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from backend.database import Base

DEFAULT_HW_REMINDERS = '[{"type": "days", "value": 1}, {"type": "hours", "value": 3}, {"type": "hours", "value": 1}]'
DEFAULT_EVENT_REMINDERS = '[{"type": "days", "value": 1}, {"type": "hours", "value": 2}]'

class UserNotificationSetting(Base):
    __tablename__ = "user_notification_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    enabled = Column(Boolean, default=True, nullable=False)

    # Hausaufgaben
    homework_new = Column(Boolean, default=True, nullable=False)
    homework_reminders = Column(Text, default=DEFAULT_HW_REMINDERS, nullable=False)
    homework_daily_reminder = Column(Boolean, default=False, nullable=False)
    homework_daily_time = Column(String, default="16:00", nullable=False)

    # Kalender & Termine
    event_new = Column(Boolean, default=True, nullable=False)
    event_reminders = Column(Text, default=DEFAULT_EVENT_REMINDERS, nullable=False)

    # Essenplan
    meal_reminder_mode = Column(String, default="same_day", nullable=False) # "same_day", "day_before", "none"
    meal_reminder_time = Column(String, default="07:00", nullable=False)

    # Stundenplan (WebUntis)
    timetable_changes = Column(Boolean, default=True, nullable=False)
    timetable_before_first_lesson = Column(Boolean, default=False, nullable=False)
    timetable_first_lesson_lead_minutes = Column(Integer, default=30, nullable=False)
    timetable_before_lesson_end = Column(Boolean, default=False, nullable=False)
    timetable_lesson_end_lead_minutes = Column(Integer, default=5, nullable=False)
    timetable_before_break = Column(Boolean, default=False, nullable=False)
    timetable_break_lead_minutes = Column(Integer, default=5, nullable=False)
    timetable_before_break_end = Column(Boolean, default=False, nullable=False)
    timetable_break_end_lead_minutes = Column(Integer, default=3, nullable=False)
    timetable_end_of_day_summary = Column(Boolean, default=True, nullable=False)
    timetable_end_of_day_delay_minutes = Column(Integer, default=60, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="notification_settings")
