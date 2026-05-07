from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from backend.database import Base

class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    short_name = Column(String, nullable=True)
    color = Column(String, default="#6750a4")
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)
    is_global = Column(Boolean, default=False)

    class_group = relationship("ClassGroup", back_populates="subjects")
    grades = relationship("Grade", back_populates="subject", cascade="all, delete-orphan")
    homework_items = relationship("Homework", back_populates="subject")
    calendar_events = relationship("CalendarEvent", back_populates="subject")
