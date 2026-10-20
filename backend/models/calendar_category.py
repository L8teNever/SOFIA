from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON
from sqlalchemy.sql import func
from backend.database import Base


class CalendarCategory(Base):
    """Eigene Kalender-Kategorie (z. B. Internat), vom Super-Admin angelegt.

    Termine dieser Kategorie sehen nur die Mitglieder (member_ids) - egal in
    welcher Klasse sie sind.
    """
    __tablename__ = "calendar_categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    color = Column(String, nullable=False, default="#6750a4")
    member_ids = Column(JSON, default=list)  # user ids
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
