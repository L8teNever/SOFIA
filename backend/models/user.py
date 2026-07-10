from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base
import enum

class UserRole(str, enum.Enum):
    student = "student"
    admin = "admin"
    super_admin = "super_admin"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    role = Column(Enum(UserRole), default=UserRole.student, nullable=False)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    push_subscription = Column(String, nullable=True)
    muted_room_ids = Column(String, default="[]", server_default="[]")
    muted_user_ids = Column(String, default="[]", server_default="[]")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    class_group = relationship("ClassGroup", back_populates="members")
    grades = relationship("Grade", back_populates="user", cascade="all, delete-orphan")
    sent_messages = relationship("Message", foreign_keys="Message.sender_id", back_populates="sender")
    shared_files = relationship("SharedFile", back_populates="uploader", cascade="all, delete-orphan")

    @property
    def name(self):
        return self.display_name or self.email.split("@")[0]
