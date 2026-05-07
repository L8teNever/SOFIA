from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Float, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class Grade(Base):
    __tablename__ = "grades"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    value = Column(Float, nullable=False)  # e.g. 1.0 – 6.0
    label = Column(String, nullable=True)  # e.g. "Mathearbeit 1"
    note = Column(Text, nullable=True)
    date = Column(String, nullable=True)   # ISO date
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="grades")
    subject = relationship("Subject", back_populates="grades")
