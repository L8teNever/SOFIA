from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class NotesBoard(Base):
    __tablename__ = "notes_boards"
    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    # Same loose free-text convention as DriveFile.topic (not an FK to
    # drive_topics) — a board and a Drive file share a "folder" purely by
    # matching subject_id + topic, no separate sync needed between the two.
    topic = Column(String, nullable=True)
    title = Column(String, nullable=False)
    is_public = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User")
    subject = relationship("Subject")
