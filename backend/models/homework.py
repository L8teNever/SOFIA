from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class Homework(Base):
    __tablename__ = "homework"
    id = Column(Integer, primary_key=True, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False)
    description = Column(Text, nullable=False)
    due_date = Column(String, nullable=False)  # ISO date YYYY-MM-DD
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    checked_by = Column(JSON, default=list)  # list of user_ids who checked it off

    subject = relationship("Subject", back_populates="homework_items")
    class_group = relationship("ClassGroup", back_populates="homework_items")
    creator = relationship("User")
