from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base
import enum

class ShareVisibility(str, enum.Enum):
    person = "person"
    people = "people"
    class_ = "class"
    all = "all"

class SharedFile(Base):
    __tablename__ = "shared_files"
    id = Column(Integer, primary_key=True, index=True)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String, nullable=True)
    visibility = Column(Enum(ShareVisibility), default=ShareVisibility.class_)
    visible_to = Column(JSON, default=list)  # list of user_ids (for person/people visibility)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    uploader = relationship("User", back_populates="shared_files")
