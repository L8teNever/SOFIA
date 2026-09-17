from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from backend.database import Base

class ChatPollVote(Base):
    """One user's vote on one poll — single-choice (the unique constraint on
    message_id+user_id means casting a new vote replaces the old one rather
    than adding a second). message_id is duplicated here alongside
    option_id purely so "did I already vote on THIS poll" and "clear my old
    vote before recording the new one" don't need a join through
    ChatPollOption first."""
    __tablename__ = "chat_poll_votes"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    option_id = Column(Integer, ForeignKey("chat_poll_options.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_chat_poll_vote_message_user"),)
