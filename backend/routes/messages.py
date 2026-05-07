from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from backend.database import get_db, AsyncSessionLocal
from backend.auth import get_current_user
from backend.models.message import ChatRoom, Message
from backend.models.user import User
from backend.schemas import ChatRoomOut, MessageOut
from typing import List, Dict
import json

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# In-memory WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active: Dict[int, List[WebSocket]] = {}

    async def connect(self, room_id: int, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(room_id, []).append(ws)

    def disconnect(self, room_id: int, ws: WebSocket):
        if room_id in self.active:
            self.active[room_id].discard(ws) if hasattr(self.active[room_id], 'discard') else None
            try: self.active[room_id].remove(ws)
            except ValueError: pass

    async def broadcast(self, room_id: int, data: dict):
        for ws in list(self.active.get(room_id, [])):
            try: await ws.send_json(data)
            except: self.active[room_id].remove(ws)

manager = ConnectionManager()

@router.get("/rooms", response_model=List[ChatRoomOut])
async def list_rooms(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(ChatRoom))
    rooms = result.scalars().all()
    return [r for r in rooms if current_user.id in (r.member_ids or [])]

@router.post("/rooms", response_model=ChatRoomOut)
async def create_room(data: dict, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    member_ids = data.get("member_ids", [])
    if current_user.id not in member_ids:
        member_ids.append(current_user.id)
    room = ChatRoom(name=data.get("name"), is_group=data.get("is_group", False), member_ids=member_ids)
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return room

@router.get("/rooms/{room_id}/messages", response_model=List[MessageOut])
async def get_messages(room_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
    room = result.scalar_one_or_none()
    if not room or current_user.id not in (room.member_ids or []):
        raise HTTPException(403)
    msgs = await db.execute(select(Message).where(Message.room_id == room_id).order_by(Message.created_at))
    return msgs.scalars().all()

@router.websocket("/ws/{room_id}")
async def ws_chat(room_id: int, ws: WebSocket):
    await manager.connect(room_id, ws)
    try:
        async with AsyncSessionLocal() as db:
            while True:
                data = await ws.receive_json()
                msg = Message(
                    room_id=room_id,
                    sender_id=data.get("sender_id"),
                    content=data.get("content"),
                    file_url=data.get("file_url"),
                    file_type=data.get("file_type", "text"),
                    read_by=[data.get("sender_id")],
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)
                await manager.broadcast(room_id, {
                    "id": msg.id, "sender_id": msg.sender_id, "content": msg.content,
                    "file_url": msg.file_url, "file_type": msg.file_type,
                    "created_at": msg.created_at.isoformat(),
                })
    except WebSocketDisconnect:
        manager.disconnect(room_id, ws)
