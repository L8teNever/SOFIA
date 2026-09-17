from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from backend.database import Base

class ChatMessageReaction(Base):
    """One user's emoji reaction to one message — single-reaction-per-user
    (the unique constraint means picking a new emoji replaces the old one
    rather than stacking a second reaction from the same person), same
    single-choice pattern ChatPollVote already uses."""
    __tablename__ = "chat_message_reactions"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    emoji = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_chat_reaction_message_user"),)
