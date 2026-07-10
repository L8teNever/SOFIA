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

def _reply_preview(msg: Message) -> dict:
    return {
        "id": msg.id, "sender_id": msg.sender_id, "content": msg.content,
        "file_type": msg.file_type, "deleted": bool(msg.deleted),
    }

@router.get("/rooms/{room_id}/messages", response_model=List[MessageOut])
async def get_messages(room_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
    room = result.scalar_one_or_none()
    if not room or current_user.id not in (room.member_ids or []):
        raise HTTPException(403)
    msgs = (await db.execute(select(Message).where(Message.room_id == room_id).order_by(Message.created_at))).scalars().all()
    by_id = {m.id: m for m in msgs}
    out = []
    for m in msgs:
        reply_preview = _reply_preview(by_id[m.reply_to_id]) if m.reply_to_id and m.reply_to_id in by_id else None
        out.append({
            "id": m.id, "room_id": m.room_id, "sender_id": m.sender_id,
            "content": m.content, "file_url": m.file_url, "file_type": m.file_type,
            "created_at": m.created_at, "read_by": m.read_by,
            "reply_to_id": m.reply_to_id, "reply_preview": reply_preview,
            "edited": bool(m.edited), "deleted": bool(m.deleted), "waveform": m.waveform,
        })
    return out

@router.websocket("/ws/{room_id}")
async def ws_chat(room_id: int, ws: WebSocket):
    await manager.connect(room_id, ws)
    try:
        async with AsyncSessionLocal() as db:
            while True:
                data = await ws.receive_json()
                action = data.get("action", "send")
                sender_id = data.get("sender_id")

                if action == "edit":
                    msg_id = data.get("message_id")
                    result = await db.execute(select(Message).where(Message.id == msg_id, Message.room_id == room_id))
                    msg = result.scalar_one_or_none()
                    if not msg or msg.sender_id != sender_id or msg.deleted or msg.file_type not in (None, "text"):
                        continue
                    msg.content = data.get("content")
                    msg.edited = True
                    await db.commit()
                    await manager.broadcast(room_id, {"type": "edit", "id": msg.id, "content": msg.content, "edited": True})
                    continue

                if action == "delete":
                    msg_id = data.get("message_id")
                    result = await db.execute(select(Message).where(Message.id == msg_id, Message.room_id == room_id))
                    msg = result.scalar_one_or_none()
                    if not msg or msg.sender_id != sender_id:
                        continue
                    msg.deleted = True
                    msg.content = None
                    msg.file_url = None
                    await db.commit()
                    await manager.broadcast(room_id, {"type": "delete", "id": msg.id})
                    continue

                reply_to_id = data.get("reply_to_id")
                msg = Message(
                    room_id=room_id,
                    sender_id=sender_id,
                    content=data.get("content"),
                    file_url=data.get("file_url"),
                    file_type=data.get("file_type", "text"),
                    read_by=[sender_id],
                    reply_to_id=reply_to_id,
                    waveform=data.get("waveform"),
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)

                reply_preview = None
                if reply_to_id:
                    r = await db.execute(select(Message).where(Message.id == reply_to_id))
                    rm = r.scalar_one_or_none()
                    if rm:
                        reply_preview = _reply_preview(rm)

                await manager.broadcast(room_id, {
                    "type": "new",
                    "id": msg.id, "sender_id": msg.sender_id, "content": msg.content,
                    "file_url": msg.file_url, "file_type": msg.file_type,
                    "created_at": msg.created_at.isoformat(),
                    "reply_to_id": msg.reply_to_id, "reply_preview": reply_preview,
                    "edited": False, "deleted": False, "waveform": msg.waveform,
                })
    except WebSocketDisconnect:
        manager.disconnect(room_id, ws)
