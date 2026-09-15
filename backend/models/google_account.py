from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from backend.database import Base

class GoogleAccount(Base):
    """One user's connected Google account — tokens are Fernet-encrypted at
    rest with the same key used for Untis passwords (backend/services/crypto.py)."""
    __tablename__ = "google_accounts"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    google_email = Column(String, nullable=True)
    access_token_enc = Column(Text, nullable=False)
    refresh_token_enc = Column(Text, nullable=False)
    token_expiry = Column(DateTime(timezone=True), nullable=True)

    sync_calendar = Column(Boolean, default=False, nullable=False)
    sync_homework_tasks = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
