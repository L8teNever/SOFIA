from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class SubjectWeighting(Base):
    __tablename__ = "subject_weightings"

    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False, index=True)
    exam_weight = Column(Float, default=50.0)      # % Klassenarbeiten
    oral_weight = Column(Float, default=50.0)      # % Mündlich / Sonstige
    planned_exams = Column(Integer, default=2)     # Geplante Arbeiten
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    class_group = relationship("ClassGroup")
    subject = relationship("Subject")
    updater = relationship("User")
