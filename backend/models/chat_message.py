from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func
from backend.database import Base

class ChatMessage(Base):
    """One message in a conversation. text is set for msg_type='text';
    file_name/file_size/mime_type/storage_filename are set for image/file/
    voice messages — storage_filename is the random on-disk name under
    settings.chat_storage_dir (same original-name/on-disk-name split Drive
    uses), file_name is what's actually shown to the user."""
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    msg_type = Column(String, nullable=False, default="text")
    text = Column(String, nullable=True)
    storage_filename = Column(String, nullable=True)
    file_name = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
