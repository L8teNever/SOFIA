from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from backend.database import Base

class GoogleSyncedEvent(Base):
    """Maps one SOFIA calendar event -> one Google Calendar event, per user
    (a class-wide event can be synced into several different users' own
    Google Calendars, each getting its own Google event id to update/delete)."""
    __tablename__ = "google_synced_events"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    calendar_event_id = Column(Integer, ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False)
    google_event_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "calendar_event_id", name="uq_gse_user_event"),)

class GoogleSyncedTask(Base):
    """Maps one SOFIA homework item -> one Google Task, per user."""
    __tablename__ = "google_synced_tasks"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    homework_id = Column(Integer, ForeignKey("homework.id", ondelete="CASCADE"), nullable=False)
    google_task_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "homework_id", name="uq_gst_user_hw"),)
