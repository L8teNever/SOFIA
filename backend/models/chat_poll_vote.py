from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from backend.database import Base

class ChatPollVote(Base):
    """One user's vote for one option on one poll. The unique constraint is
    on (message_id, option_id, user_id) rather than just (message_id,
    user_id) so a poll with poll_multi=True can hold several rows per user
    — one per option they picked — while a single-choice poll still gets
    "replace, don't add" behavior at the route level (delete the user's
    existing row(s) for that message before inserting the new one).
    message_id is duplicated here alongside option_id purely so "did I
    already vote on THIS poll" doesn't need a join through ChatPollOption
    first."""
    __tablename__ = "chat_poll_votes"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    option_id = Column(Integer, ForeignKey("chat_poll_options.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("message_id", "option_id", "user_id", name="uq_chat_poll_vote_message_option_user"),)
