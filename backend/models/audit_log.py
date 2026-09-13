from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String, nullable=True)
    user_role = Column(String, nullable=True)
    action = Column(String, nullable=False, index=True)   # e.g. 'homework.create', 'file.upload', 'security.malware_detected'
    entity_type = Column(String, nullable=True, index=True) # e.g. 'homework', 'grade', 'calendar', 'user'
    entity_id = Column(String, nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    user = relationship("User")
