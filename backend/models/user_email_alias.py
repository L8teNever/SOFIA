from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func
from backend.database import Base

class UserEmailAlias(Base):
    """A secondary email address that resolves to an existing user account —
    lets a super-admin register a second Cloudflare Access identity (e.g. a
    second device/login) that should land on the same SOFIA account instead
    of being treated as a separate user."""
    __tablename__ = "user_email_aliases"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
