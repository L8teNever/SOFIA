from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, Boolean, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=True)
    is_group = Column(Boolean, default=False)
    member_ids = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    messages = relationship("Message", back_populates="room", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("chat_rooms.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=True)
    file_url = Column(String, nullable=True)
    file_type = Column(String, nullable=True)  # text|image|audio|file
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    read_by = Column(JSON, default=list)
    reply_to_id = Column(Integer, ForeignKey("messages.id"), nullable=True)
    edited = Column(Boolean, default=False)
    deleted = Column(Boolean, default=False)
    waveform = Column(JSON, nullable=True)
    poll_data = Column(JSON, nullable=True)
    reactions = Column(JSON, nullable=True)  # {"👍": [user_id, ...], ...} — one emoji per user

    room = relationship("ChatRoom", back_populates="messages")
    sender = relationship("User", back_populates="sent_messages", foreign_keys=[sender_id])
