from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from backend.database import Base

class SentNotificationLog(Base):
    __tablename__ = "sent_notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    notification_key = Column(String, unique=True, index=True, nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
