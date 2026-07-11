from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from backend.database import get_db, AsyncSessionLocal
from backend.auth import get_current_user
from backend.models.message import ChatRoom, Message
from backend.models.user import User
from backend.models.notification import Notification
from backend.schemas import ChatRoomOut, MessageOut
from backend.config import settings
from typing import List, Dict
import json, asyncio, logging, os, uuid, aiofiles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# Chat attachments (images, files, voice notes) get their own storage —
# they must NOT go through the QuickShare endpoint, which lists uploads
# publicly (visibility="all") and expires them after ~2h.
@router.post("/upload")
async def upload_chat_file(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    if file.size and file.size > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß")
    ext = os.path.splitext(file.filename or "")[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    chat_dir = os.path.join(settings.upload_dir, "chat")
    os.makedirs(chat_dir, exist_ok=True)
    async with aiofiles.open(os.path.join(chat_dir, filename), "wb") as out:
        content = await file.read()
        await out.write(content)
    return {"url": f"/uploads/chat/{filename}", "name": file.filename}

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
    user_rooms = [r for r in rooms if current_user.id in (r.member_ids or [])]
    try:
        muted_ids = json.loads(current_user.muted_room_ids or "[]")
    except:
        muted_ids = []
    
    out = []
    for r in user_rooms:
        out.append(ChatRoomOut(
            id=r.id,
            name=r.name,
            is_group=r.is_group,
            member_ids=r.member_ids,
            is_muted=r.id in muted_ids
        ))
    return out

@router.post("/rooms", response_model=ChatRoomOut)
async def create_room(data: dict, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    member_ids = data.get("member_ids", [])
    if current_user.id not in member_ids:
        member_ids.append(current_user.id)
    room = ChatRoom(name=data.get("name"), is_group=data.get("is_group", False), member_ids=member_ids)
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return ChatRoomOut(
        id=room.id,
        name=room.name,
        is_group=room.is_group,
        member_ids=room.member_ids,
        is_muted=False
    )

@router.post("/rooms/{room_id}/mute")
async def mute_room(room_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        muted = json.loads(current_user.muted_room_ids or "[]")
    except:
        muted = []
    if room_id not in muted:
        muted.append(room_id)
        current_user.muted_room_ids = json.dumps(muted)
        await db.commit()
    return {"ok": True}

@router.post("/rooms/{room_id}/unmute")
async def unmute_room(room_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        muted = json.loads(current_user.muted_room_ids or "[]")
    except:
        muted = []
    if room_id in muted:
        muted.remove(room_id)
        current_user.muted_room_ids = json.dumps(muted)
        await db.commit()
    return {"ok": True}

@router.post("/users/{user_id}/mute")
async def mute_user(user_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        muted = json.loads(current_user.muted_user_ids or "[]")
    except:
        muted = []
    if user_id not in muted:
        muted.append(user_id)
        current_user.muted_user_ids = json.dumps(muted)
        await db.commit()
    return {"ok": True}

@router.post("/users/{user_id}/unmute")
async def unmute_user(user_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        muted = json.loads(current_user.muted_user_ids or "[]")
    except:
        muted = []
    if user_id in muted:
        muted.remove(user_id)
        current_user.muted_user_ids = json.dumps(muted)
        await db.commit()
    return {"ok": True}

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
            "poll_data": m.poll_data, "reactions": m.reactions,
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

                if action == "react":
                    msg_id = data.get("message_id")
                    emoji = data.get("emoji")
                    result = await db.execute(select(Message).where(Message.id == msg_id, Message.room_id == room_id))
                    msg = result.scalar_one_or_none()
                    if not msg or msg.deleted or not emoji:
                        continue
                    rdata = json.loads(json.dumps(msg.reactions or {}))
                    # One reaction per user: clear any of their existing reactions first
                    was_set = sender_id in rdata.get(emoji, [])
                    for other_emoji in list(rdata.keys()):
                        if sender_id in rdata[other_emoji]:
                            rdata[other_emoji].remove(sender_id)
                            if not rdata[other_emoji]:
                                del rdata[other_emoji]
                    if not was_set:
                        rdata.setdefault(emoji, []).append(sender_id)
                    msg.reactions = rdata
                    await db.commit()
                    await manager.broadcast(room_id, {"type": "reaction", "id": msg.id, "reactions": msg.reactions})
                    continue

                if action == "vote":
                    msg_id = data.get("message_id")
                    option_idx = str(data.get("option_index"))
                    result = await db.execute(select(Message).where(Message.id == msg_id, Message.room_id == room_id))
                    msg = result.scalar_one_or_none()
                    if not msg or msg.deleted or msg.file_type != "poll" or not msg.poll_data:
                        continue
                    pdata = json.loads(json.dumps(msg.poll_data))
                    votes = pdata.get("votes", {})
                    if option_idx not in votes:
                        votes[option_idx] = []
                    if sender_id in votes[option_idx]:
                        votes[option_idx].remove(sender_id)
                    else:
                        votes[option_idx].append(sender_id)
                    pdata["votes"] = votes
                    msg.poll_data = pdata
                    await db.commit()
                    await manager.broadcast(room_id, {"type": "vote", "id": msg.id, "poll_data": msg.poll_data})
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
                    poll_data=data.get("poll_data"),
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)

                # Create and persist notification, and trigger push for each member of the room
                try:
                    result_room = await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))
                    room = result_room.scalar_one_or_none()
                    if room:
                        sender_res = await db.execute(select(User).where(User.id == sender_id))
                        sender_user = sender_res.scalar_one_or_none()
                        sender_name = sender_user.name if sender_user else "Jemand"
                        
                        # Notification title
                        notif_title = f"{sender_name} (in {room.name})" if room.is_group and room.name else sender_name
                        
                        # Notification body
                        if msg.file_type == "voice":
                            notif_body = "🎤 Sprachnachricht"
                        elif msg.file_type == "audio":
                            notif_body = "🎤 Sprachnachricht"
                        elif msg.file_type == "image":
                            notif_body = "📷 Bild"
                        elif msg.file_type == "video":
                            notif_body = "🎥 Video"
                        elif msg.file_type == "file":
                            notif_body = "📁 Datei"
                        elif msg.file_type == "poll":
                            notif_body = f"📊 Umfrage: {msg.content}"
                        else:
                            notif_body = msg.content or ""
                        
                        for member_id in room.member_ids:
                            if member_id == sender_id:
                                continue
                            
                            member_res = await db.execute(select(User).where(User.id == member_id))
                            member = member_res.scalar_one_or_none()
                            if not member:
                                continue
                            
                            try:
                                muted_rooms = json.loads(member.muted_room_ids or "[]")
                            except:
                                muted_rooms = []
                            try:
                                muted_users = json.loads(member.muted_user_ids or "[]")
                            except:
                                muted_users = []
                            
                            if room_id in muted_rooms or sender_id in muted_users:
                                continue
                            
                            # Add notification to database
                            db_notif = Notification(user_id=member_id, title=notif_title, body=notif_body, is_read=False)
                            db.add(db_notif)
                            
                            # Trigger Web Push if subscribed
                            if member.push_subscription:
                                from pywebpush import webpush, WebPushException
                                try:
                                    sub = json.loads(member.push_subscription)
                                    async def send_webpush_bg(sub_info, t, b, r_id):
                                        try:
                                            await asyncio.to_thread(
                                                webpush,
                                                subscription_info=sub_info,
                                                data=json.dumps({
                                                    "title": t,
                                                    "body": b,
                                                    "url": f"/chat/{r_id}"
                                                }),
                                                vapid_private_key=settings.vapid_private_key,
                                                vapid_claims={"sub": settings.vapid_claim_email},
                                            )
                                        except Exception as e:
                                            logger.warning("Webpush background send failed: %s", e)
                                    asyncio.create_task(send_webpush_bg(sub, notif_title, notif_body, room_id))
                                except Exception as e:
                                    logger.warning("Failed to start webpush task for user %s: %s", member_id, e)
                        await db.commit()
                except Exception as e:
                    logger.error("Failed to process notifications for message: %s", e)

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
                    "poll_data": msg.poll_data, "reactions": msg.reactions,
                })
    except WebSocketDisconnect:
        manager.disconnect(room_id, ws)
