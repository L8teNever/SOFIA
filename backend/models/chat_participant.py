from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from backend.database import Base

class ChatParticipant(Base):
    """Membership row for one user in one conversation — the actual
    authorization boundary for chat: every conversation/message/file route
    checks for a matching row here, not just "same class" (a class can have
    several unrelated group chats going at once). last_read_at (null until
    the first read) drives the unread-count badge."""
    __tablename__ = "chat_participants"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    last_read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_chat_participant_conv_user"),)
