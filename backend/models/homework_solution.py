from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class HomeworkSolution(Base):
    __tablename__ = "homework_solutions"

    id = Column(Integer, primary_key=True, index=True)
    homework_id = Column(Integer, ForeignKey("homework.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=True)
    attachments = Column(JSON, default=list)  # list of {"url": str, "type": str, "name": str}
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    homework = relationship("Homework", back_populates="solutions")
    user = relationship("User")
