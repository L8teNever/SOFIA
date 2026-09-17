from sqlalchemy import Column, Integer, String, ForeignKey
from backend.database import Base

class ChatPollOption(Base):
    """One selectable answer on a poll message (ChatMessage with
    msg_type='poll'). position keeps the options in the order they were
    created in, since SQL result order isn't guaranteed otherwise."""
    __tablename__ = "chat_poll_options"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    option_text = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)
