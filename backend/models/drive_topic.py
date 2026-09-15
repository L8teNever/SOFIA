from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from backend.database import Base

class DriveTopic(Base):
    """A named subfolder within a subject's Drive folder. Kept as its own
    row (rather than only inferring folders from files' topic strings) so a
    user can create an empty folder ahead of uploading into it, and so
    renaming a folder is one edit instead of a batch text find/replace."""
    __tablename__ = "drive_topics"
    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("subject_id", "name", name="uq_drive_topic_subject_name"),)
