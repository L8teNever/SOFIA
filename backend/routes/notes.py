from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, delete
from backend.database import get_db, AsyncSessionLocal
from backend.auth import get_current_user, get_current_user_ws
from backend.models.notes_board import NotesBoard
from backend.models.notes_stroke import NotesStroke
from backend.models.drive_topic import DriveTopic
from backend.models.subject import Subject
from backend.models.user import User
from backend.schemas import NotesBoardOut, NotesBoardCreate, NotesBoardUpdate
from backend.services.notes_connection_manager import NotesConnectionManager
from typing import List, Optional
import json

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])
manager = NotesConnectionManager()

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

def _board_visible(board: NotesBoard, user: User) -> bool:
    return board.is_public or board.owner_id == user.id or user.role in ("admin", "super_admin")

# --- Live drawing --- one connection = one already-scoped board, so unlike
# a single multiplexed endpoint, no boardId needs threading into individual
# messages — only into the URL and the connection-manager's broadcast scope.
# Message protocol below is an unchanged port of sofianotes' own (see
# C:\tmp\sofianotes-explore\backend\app\main.py) plus a ping/pong keepalive
# it didn't have, cheap insurance against a Cloudflare Tunnel idle timeout.
@router.websocket("/ws/{board_id}")
async def notes_ws(websocket: WebSocket, board_id: int):
    # Auth-in-handshake has no in-repo precedent (first WebSocket route in
    # this codebase) — called as plain functions, not via Depends(), to
    # sidestep FastAPI's Request-vs-WebSocket dependency typing ambiguity.
    #
    # accept() has to happen BEFORE any rejection: closing pre-accept still
    # sends the intended close code at the ASGI level, but browsers only
    # ever surface a generic 1006 to JS for anything that closes before the
    # opening handshake completes — verified against this exact FastAPI/
    # Starlette pin, not assumed. Accepting first (then immediately closing
    # on failure) is also consistent with this codebase's existing
    # never-distinguish-404-from-403 convention elsewhere (_get_visible in
    # drive.py) — a rejected probe looks identical at the network level to
    # an accepted-then-closed one either way.
    await websocket.accept()
    async with AsyncSessionLocal() as db:
        user = await get_current_user_ws(websocket, db)
        if user is None:
            await websocket.close(code=4401)
            return
        result = await db.execute(select(NotesBoard).where(NotesBoard.id == board_id))
        board = result.scalar_one_or_none()
        if not board or board.class_id != user.class_id:
            await websocket.close(code=4404)
            return
        if not _board_visible(board, user):
            await websocket.close(code=4403)
            return

    display_name = user.display_name or user.email.split("@")[0]
    client = manager.connect(board_id, websocket, user.id, display_name)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(NotesStroke).where(NotesStroke.board_id == board_id).order_by(NotesStroke.created_at)
        )
        strokes = [
            {"id": s.id, "tool": s.tool, "color": s.color, "size": s.size, "points": json.loads(s.points)}
            for s in result.scalars().all()
        ]
    await websocket.send_json({"type": "init", "clientId": client.id, "color": client.color, "strokes": strokes})
    await manager.broadcast(board_id, {
        "type": "presence_join", "id": client.id, "color": client.color,
        "userId": client.user_id, "displayName": client.display_name,
    }, exclude=websocket)

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "cursor":
                await manager.broadcast(board_id, {
                    "type": "cursor", "id": client.id, "color": client.color,
                    "userId": client.user_id, "displayName": client.display_name,
                    "x": msg.get("x"), "y": msg.get("y"), "tool": msg.get("tool"), "size": msg.get("size"),
                }, exclude=websocket)

            elif msg_type == "stroke_start":
                stroke_id = msg.get("strokeId")
                if not stroke_id:
                    continue
                client.in_progress[stroke_id] = {
                    "id": stroke_id, "tool": msg.get("tool", "pen"), "color": msg.get("color", "#000000"),
                    "size": msg.get("size", 4), "points": list(msg.get("points", [])),
                }
                entry = client.in_progress[stroke_id]
                await manager.broadcast(board_id, {
                    "type": "stroke_start", "id": client.id, "strokeId": stroke_id,
                    "tool": entry["tool"], "color": entry["color"], "size": entry["size"], "points": entry["points"],
                }, exclude=websocket)

            elif msg_type == "stroke_points":
                stroke_id = msg.get("strokeId")
                entry = client.in_progress.get(stroke_id)
                new_points = msg.get("points", [])
                if entry is not None:
                    entry["points"].extend(new_points)
                await manager.broadcast(board_id, {
                    "type": "stroke_points", "id": client.id, "strokeId": stroke_id, "points": new_points,
                }, exclude=websocket)

            elif msg_type == "stroke_end":
                stroke_id = msg.get("strokeId")
                entry = client.in_progress.pop(stroke_id, None)
                if entry is not None and len(entry["points"]) >= 1:
                    async with AsyncSessionLocal() as db:
                        await db.merge(NotesStroke(
                            id=entry["id"], board_id=board_id, tool=entry["tool"],
                            color=entry["color"], size=entry["size"], points=json.dumps(entry["points"]),
                        ))
                        await db.commit()
                await manager.broadcast(board_id, {"type": "stroke_end", "id": client.id, "strokeId": stroke_id}, exclude=websocket)

            elif msg_type == "stroke_replace":
                stroke_id = msg.get("strokeId")
                entry = client.in_progress.get(stroke_id)
                new_points = msg.get("points", [])
                if entry is not None:
                    entry["points"] = new_points
                await manager.broadcast(board_id, {
                    "type": "stroke_replace", "id": client.id, "strokeId": stroke_id, "points": new_points,
                }, exclude=websocket)

            elif msg_type == "stroke_move":
                stroke = msg.get("stroke")
                if stroke and stroke.get("id"):
                    async with AsyncSessionLocal() as db:
                        await db.merge(NotesStroke(
                            id=stroke["id"], board_id=board_id, tool=stroke.get("tool", "pen"),
                            color=stroke.get("color", "#000000"), size=stroke.get("size", 4),
                            points=json.dumps(stroke.get("points", [])),
                        ))
                        await db.commit()
                    await manager.broadcast(board_id, {"type": "stroke_move", "id": client.id, "stroke": stroke}, exclude=websocket)

            elif msg_type == "stroke_abort":
                stroke_id = msg.get("strokeId")
                client.in_progress.pop(stroke_id, None)
                await manager.broadcast(board_id, {"type": "stroke_abort", "id": client.id, "strokeId": stroke_id}, exclude=websocket)

            elif msg_type == "erase":
                stroke_ids = [s for s in msg.get("strokeIds", []) if s]
                if stroke_ids:
                    async with AsyncSessionLocal() as db:
                        await db.execute(delete(NotesStroke).where(NotesStroke.board_id == board_id, NotesStroke.id.in_(stroke_ids)))
                        await db.commit()
                    await manager.broadcast(board_id, {"type": "erase", "id": client.id, "strokeIds": stroke_ids}, exclude=websocket)

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(board_id, websocket)
        await manager.broadcast(board_id, {"type": "presence_leave", "id": client.id})
