from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class DriveFile(Base):
    __tablename__ = "drive_files"
    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    topic = Column(String, nullable=True)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String, nullable=True)
    # Cross-cutting flag (not tied to a subject/topic) — a file recognized as
    # lecture/class notes shows up both under its normal date-topic folder
    # AND in a subject's "Aufschriebe" group, rather than needing a whole
    # multi-topic model for one specific cross-cutting category.
    is_lecture_notes = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    uploader = relationship("User")
    subject = relationship("Subject")
