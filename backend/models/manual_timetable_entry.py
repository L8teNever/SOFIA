from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from backend.database import Base

class ManualTimetableEntry(Base):
    """One recurring weekly lesson slot for a class's photo-recognized ('manual')
    timetable — the fallback used when WebUntis isn't available. Unlike Untis
    lessons these have no live cancellation/substitution data, just a fixed
    weekday + time + subject + room, projected onto real dates on read."""
    __tablename__ = "manual_timetable_entries"
    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=Montag .. 4=Freitag
    start_time = Column(Integer, nullable=False)  # HHMM, e.g. 800
    end_time = Column(Integer, nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    room = Column(String, nullable=True)

    class_group = relationship("ClassGroup", back_populates="manual_timetable_entries")
    subject = relationship("Subject")
