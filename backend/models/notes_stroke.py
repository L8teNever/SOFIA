from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class NotesStroke(Base):
    __tablename__ = "notes_strokes"
    # The client-generated stroke UUID doubles as the primary key, so a
    # stroke_move message (used for both lasso-drag and undo/redo replay)
    # maps directly onto a single upsert-by-id instead of a separate lookup.
    id = Column(String, primary_key=True, index=True)
    # ondelete="CASCADE" is declarative-only here — this codebase never
    # enables PRAGMA foreign_keys, so it will NOT actually fire. Board
    # deletion must explicitly bulk-delete matching strokes first.
    board_id = Column(Integer, ForeignKey("notes_boards.id", ondelete="CASCADE"), nullable=False, index=True)
    tool = Column(String, nullable=False)
    color = Column(String, nullable=False)
    size = Column(Float, nullable=False)
    points = Column(Text, nullable=False)  # JSON-encoded [{x,y,p}, ...]
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    board = relationship("NotesBoard", backref="strokes")
