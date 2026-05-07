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

    members = relationship("User", back_populates="class_group")
    subjects = relationship("Subject", back_populates="class_group", cascade="all, delete-orphan")
    calendar_events = relationship("CalendarEvent", back_populates="class_group", cascade="all, delete-orphan")
    homework_items = relationship("Homework", back_populates="class_group", cascade="all, delete-orphan")
