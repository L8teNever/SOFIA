from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.sql import func
from backend.database import Base

class ChatMessage(Base):
    """One message in a conversation. text is set for msg_type='text';
    file_name/file_size/mime_type/storage_filename are set for image/file/
    voice messages — storage_filename is the random on-disk name under
    settings.chat_storage_dir (same original-name/on-disk-name split Drive
    uses), file_name is what's actually shown to the user. poll_multi only
    applies to msg_type='poll' — whether voters may pick more than one
    option (see ChatPollVote's unique constraint)."""
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
    reply_to_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    poll_multi = Column(Boolean, default=False, nullable=False)
    # A GIF picked from the GIPHY library lives on GIPHY's CDN —
    # external_url points straight at it instead of storage_filename, so we
    # never download and re-host something that's already meant to be
    # publicly embeddable.
    external_url = Column(String, nullable=True)
    is_edited = Column(Boolean, default=False, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
