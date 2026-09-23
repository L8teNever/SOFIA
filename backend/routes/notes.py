from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, delete
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.notes_board import NotesBoard
from backend.models.notes_stroke import NotesStroke
from backend.models.drive_topic import DriveTopic
from backend.models.subject import Subject
from backend.models.user import User
from backend.schemas import NotesBoardOut, NotesBoardCreate, NotesBoardUpdate
from typing import List, Optional

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])

# Duplicated from drive.py's private helper of the same name rather than
# shared across files — this codebase already accepts this kind of small
# per-route-file duplication (each route file keeps its own _serialize too).
async def _get_or_create_topic(db: AsyncSession, class_id: int, subject_id: int, name: str) -> DriveTopic:
    name = name.strip()
    result = await db.execute(select(DriveTopic).where(DriveTopic.subject_id == subject_id, DriveTopic.name == name))
    topic = result.scalar_one_or_none()
    if topic:
        return topic
    topic = DriveTopic(class_id=class_id, subject_id=subject_id, name=name)
    db.add(topic)
    await db.flush()
    return topic

async def _serialize(db: AsyncSession, boards: List[NotesBoard]) -> List[dict]:
    if not boards:
        return []
    owner_ids = {b.owner_id for b in boards}
    subject_ids = {b.subject_id for b in boards if b.subject_id}
    owners = {}
    if owner_ids:
        res = await db.execute(select(User).where(User.id.in_(owner_ids)))
        owners = {u.id: u for u in res.scalars().all()}
    subjects = {}
    if subject_ids:
        res = await db.execute(select(Subject).where(Subject.id.in_(subject_ids)))
        subjects = {s.id: s for s in res.scalars().all()}

    out = []
    for b in boards:
        o = owners.get(b.owner_id)
        s = subjects.get(b.subject_id) if b.subject_id else None
        out.append({
            "id": b.id,
            "class_id": b.class_id,
            "owner_id": b.owner_id,
            "owner_name": (o.display_name or o.email.split("@")[0]) if o else None,
            "subject_id": b.subject_id,
            "subject_name": s.name if s else None,
            "topic": b.topic,
            "title": b.title,
            "is_public": b.is_public,
            "created_at": b.created_at,
            "updated_at": b.updated_at,
        })
    return out

async def _get_owned_or_admin(db: AsyncSession, board_id: int, current_user: User) -> NotesBoard:
    result = await db.execute(select(NotesBoard).where(NotesBoard.id == board_id))
    board = result.scalar_one_or_none()
    if not board or board.class_id != current_user.class_id:
        raise HTTPException(404)
    if board.owner_id != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)
    return board

@router.get("/boards", response_model=List[NotesBoardOut])
async def list_notes_boards(scope: str = "mine", db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        return []
    if scope == "class":
        # Powers Drive's cross-linking view only — a board with no
        # subject/topic is a personal scratch board and never shows in Drive.
        clauses = [
            NotesBoard.class_id == current_user.class_id,
            NotesBoard.subject_id.isnot(None),
        ]
        if current_user.role not in ("admin", "super_admin"):
            clauses.append(or_(NotesBoard.is_public == True, NotesBoard.owner_id == current_user.id))
        result = await db.execute(select(NotesBoard).where(*clauses).order_by(NotesBoard.updated_at.desc()))
    else:
        result = await db.execute(
            select(NotesBoard).where(NotesBoard.owner_id == current_user.id).order_by(NotesBoard.updated_at.desc())
        )
    return await _serialize(db, list(result.scalars().all()))

@router.post("/boards", response_model=NotesBoardOut)
async def create_notes_board(data: NotesBoardCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        raise HTTPException(400, "Keine Klasse zugeordnet")
    topic_name = data.topic.strip() if data.topic else None
    if data.subject_id and topic_name:
        await _get_or_create_topic(db, current_user.class_id, data.subject_id, topic_name)
    board = NotesBoard(
        class_id=current_user.class_id,
        owner_id=current_user.id,
        subject_id=data.subject_id,
        topic=topic_name,
        title=data.title.strip() or "Neues Board",
        is_public=data.is_public,
    )
    db.add(board)
    await db.commit()
    await db.refresh(board)
    out = await _serialize(db, [board])
    return out[0]

@router.get("/boards/{board_id}", response_model=NotesBoardOut)
async def get_notes_board(board_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(NotesBoard).where(NotesBoard.id == board_id))
    board = result.scalar_one_or_none()
    if not board or board.class_id != current_user.class_id:
        raise HTTPException(404)
    if not board.is_public and board.owner_id != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(404)
    out = await _serialize(db, [board])
    return out[0]

@router.put("/boards/{board_id}", response_model=NotesBoardOut)
async def update_notes_board(board_id: int, data: NotesBoardUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    board = await _get_owned_or_admin(db, board_id, current_user)
    if data.title is not None:
        board.title = data.title.strip() or board.title
    if data.subject_id is not None:
        board.subject_id = data.subject_id
    if data.topic is not None:
        topic_name = data.topic.strip() or None
        board.topic = topic_name
        if board.subject_id and topic_name:
            await _get_or_create_topic(db, current_user.class_id, board.subject_id, topic_name)
    if data.is_public is not None:
        board.is_public = data.is_public
    await db.commit()
    await db.refresh(board)
    out = await _serialize(db, [board])
    return out[0]

@router.delete("/boards/{board_id}")
async def delete_notes_board(board_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    board = await _get_owned_or_admin(db, board_id, current_user)
    # SQLite's FK cascade is declarative-only in this codebase (PRAGMA
    # foreign_keys is never enabled) — strokes must be deleted explicitly.
    await db.execute(delete(NotesStroke).where(NotesStroke.board_id == board_id))
    await db.delete(board)
    await db.commit()
    return {"ok": True}
