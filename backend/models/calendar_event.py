from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base
import enum

class EventType(str, enum.Enum):
    exam = "exam"
    trip = "trip"
    other = "other"
    personal = "personal"

class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    date = Column(String, nullable=False)  # ISO date YYYY-MM-DD
    time = Column(String, nullable=True)   # HH:MM optional
    event_type = Column(Enum(EventType), default=EventType.other)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    class_group = relationship("ClassGroup", back_populates="calendar_events")
    subject = relationship("Subject", back_populates="calendar_events")
    creator = relationship("User")
