from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.sql import func
from backend.database import Base

class ChatConversation(Base):
    """A 1:1 or group conversation between users of the same class. Who's
    actually in it lives in ChatParticipant, not here — this row only holds
    the conversation's own metadata (group name, who started it)."""
    __tablename__ = "chat_conversations"
    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False)
    is_group = Column(Boolean, default=False, nullable=False)
    name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    is_notes = Column(Boolean, default=False, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
