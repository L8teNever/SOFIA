from sqlalchemy import Column, Integer, String, Table, ForeignKey
from sqlalchemy.orm import relationship
from backend.database import Base

shared_subjects = Table(
    "shared_subjects",
    Base.metadata,
    Column("class_id", Integer, ForeignKey("class_groups.id"), primary_key=True),
    Column("subject_id", Integer, ForeignKey("subjects.id"), primary_key=True),
)

class ClassGroup(Base):
    __tablename__ = "class_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    untis_url = Column(String, nullable=True)
    untis_school = Column(String, nullable=True)
    untis_class = Column(String, nullable=True)
    untis_user = Column(String, nullable=True)
    untis_password_enc = Column(String, nullable=True)
    # 'untis' (default) or 'manual' — which source get_timetable() serves.
    # Independent of whether untis_url is set, so admins can flip back and
    # forth without losing either the Untis credentials or an uploaded photo
    # timetable's recognized entries.
    timetable_source = Column(String, nullable=False, default="untis")

    members = relationship("User", back_populates="class_group")
    subjects = relationship("Subject", back_populates="class_group", cascade="all, delete-orphan")
    calendar_events = relationship("CalendarEvent", back_populates="class_group", cascade="all, delete-orphan")
    homework_items = relationship("Homework", back_populates="class_group", cascade="all, delete-orphan")
    manual_timetable_entries = relationship("ManualTimetableEntry", back_populates="class_group", cascade="all, delete-orphan")
