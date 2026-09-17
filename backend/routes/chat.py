from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.chat_conversation import ChatConversation
from backend.models.chat_participant import ChatParticipant
from backend.models.chat_message import ChatMessage
from backend.models.chat_poll_option import ChatPollOption
from backend.models.chat_poll_vote import ChatPollVote
from backend.models.chat_message_reaction import ChatMessageReaction
from backend.models.user import User
from backend.schemas import (
    ChatConversationOut, ChatConversationCreate, ChatParticipantOut, ChatMessageOut, ChatMessageCreate,
    ChatMuteUpdate, ChatPollOptionOut, ChatPollOut, ChatVoteCreate,
    ChatReplyPreviewOut, ChatReactionOut, ChatReactionCreate,
    ChatMessageUpdate, ChatGroupUpdate,
)
from backend.routes.vapid import push_to_users
from backend.services.notification_scheduler import get_user_settings
from backend.config import settings
from backend.services.virus_scanner import scan_file
from backend.routes.users import _compress_avatar, AVATAR_DIR_NAME
from datetime import datetime, timezone
from typing import List, Optional
import logging, os, uuid, aiofiles, httpx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

async def _require_participant(db: AsyncSession, conversation_id: int, current_user: User) -> ChatConversation:
    """The actual authorization boundary for chat — a participant row for
    THIS conversation, not just "same class" (a class can have several
    unrelated group chats going at once, so class membership alone would
    leak messages across them)."""
    result = await db.execute(select(ChatParticipant).where(
        ChatParticipant.conversation_id == conversation_id, ChatParticipant.user_id == current_user.id,
    ))
    if not result.scalar_one_or_none():
        raise HTTPException(404)
    conv_result = await db.execute(select(ChatConversation).where(ChatConversation.id == conversation_id))
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404)
    return conv

async def _participants_out(db: AsyncSession, conversation_id: int) -> List[ChatParticipantOut]:
    result = await db.execute(
        select(User).join(ChatParticipant, ChatParticipant.user_id == User.id)
        .where(ChatParticipant.conversation_id == conversation_id)
    )
    return [ChatParticipantOut(user_id=u.id, display_name=u.name, avatar_url=u.avatar_url) for u in result.scalars().all()]

async def _polls_out(db: AsyncSession, messages: List[ChatMessage], current_user: User) -> dict:
    """Builds poll data (options + vote counts + the requester's own vote)
    for every poll message in one batch — called once per list_messages
    request rather than once per message, so a 100-message page with a few
    polls in it doesn't turn into a query storm."""
    poll_msg_ids = [m.id for m in messages if m.msg_type == "poll"]
    if not poll_msg_ids:
        return {}

    opts_result = await db.execute(
        select(ChatPollOption).where(ChatPollOption.message_id.in_(poll_msg_ids)).order_by(ChatPollOption.position)
    )
    options_by_msg: dict[int, list[ChatPollOption]] = {}
    for opt in opts_result.scalars().all():
        options_by_msg.setdefault(opt.message_id, []).append(opt)

    votes_result = await db.execute(select(ChatPollVote).where(ChatPollVote.message_id.in_(poll_msg_ids)))
    votes = list(votes_result.scalars().all())
    counts: dict[int, int] = {}
    my_votes: dict[int, list[int]] = {}
    for v in votes:
        counts[v.option_id] = counts.get(v.option_id, 0) + 1
        if v.user_id == current_user.id:
            my_votes.setdefault(v.message_id, []).append(v.option_id)

    out = {}
    for msg in messages:
        if msg.msg_type != "poll":
            continue
        opts = options_by_msg.get(msg.id, [])
        total = sum(counts.get(o.id, 0) for o in opts)
        out[msg.id] = ChatPollOut(
            question=msg.text or "",
            options=[ChatPollOptionOut(id=o.id, option_text=o.option_text, vote_count=counts.get(o.id, 0)) for o in opts],
            my_votes=my_votes.get(msg.id, []),
            allow_multiple=bool(msg.poll_multi),
            total_votes=total,
        )
    return out

async def _reply_previews_out(db: AsyncSession, messages: List[ChatMessage]) -> dict:
    """A small quoted preview of whatever message each of these is replying
    to (if any) — batched the same way _polls_out is, one query for the
    whole page of messages instead of one per reply."""
    reply_ids = {m.reply_to_id for m in messages if m.reply_to_id}
    if not reply_ids:
        return {}
    result = await db.execute(select(ChatMessage).where(ChatMessage.id.in_(reply_ids)))
    originals = {m.id: m for m in result.scalars().all()}
    sender_ids = {m.sender_id for m in originals.values()}
    senders = {}
    if sender_ids:
        sres = await db.execute(select(User).where(User.id.in_(sender_ids)))
        senders = {u.id: u.name for u in sres.scalars().all()}

    out = {}
    for m in messages:
        if not m.reply_to_id or m.reply_to_id not in originals:
            continue
        orig = originals[m.reply_to_id]
        out[m.id] = ChatReplyPreviewOut(
            id=orig.id, sender_name=senders.get(orig.sender_id, "?"), msg_type=orig.msg_type, text=orig.text,
        )
    return out

async def _reactions_out(db: AsyncSession, messages: List[ChatMessage], current_user: User) -> dict:
    """Reactions grouped by (message, emoji) with a count and whether the
    requester is among them — same batched-in-one-query shape as polls."""
    msg_ids = [m.id for m in messages]
    if not msg_ids:
        return {}
    result = await db.execute(select(ChatMessageReaction).where(ChatMessageReaction.message_id.in_(msg_ids)))
    reactions = list(result.scalars().all())

    grouped: dict[tuple, dict] = {}
    for r in reactions:
        key = (r.message_id, r.emoji)
        if key not in grouped:
            grouped[key] = {"count": 0, "mine": False}
        grouped[key]["count"] += 1
        if r.user_id == current_user.id:
            grouped[key]["mine"] = True

    out: dict[int, list] = {}
    for (msg_id, emoji), data in grouped.items():
        out.setdefault(msg_id, []).append(ChatReactionOut(emoji=emoji, count=data["count"], reacted_by_me=data["mine"]))
    return out

@router.get("/conversations", response_model=List[ChatConversationOut])
async def list_conversations(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.class_id:
        notes_check = await db.execute(
            select(ChatConversation.id).join(ChatParticipant, ChatParticipant.conversation_id == ChatConversation.id)
            .where(ChatParticipant.user_id == current_user.id, ChatConversation.is_notes == True)
        )
        if not notes_check.scalar_one_or_none():
            notes_conv = ChatConversation(
                class_id=current_user.class_id, is_group=False, is_notes=True,
                name="Notizen", created_by=current_user.id,
            )
            db.add(notes_conv)
            await db.flush()
            db.add(ChatParticipant(conversation_id=notes_conv.id, user_id=current_user.id))
            await db.commit()

    my_rows = await db.execute(select(ChatParticipant).where(ChatParticipant.user_id == current_user.id))
    my_participations = list(my_rows.scalars().all())
    if not my_participations:
        return []

    out = []
    for part in my_participations:
        conv_result = await db.execute(select(ChatConversation).where(ChatConversation.id == part.conversation_id))
        conv = conv_result.scalar_one_or_none()
        if not conv:
            continue

        participants = await _participants_out(db, conv.id)

        last_msg_result = await db.execute(
            select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.created_at.desc()).limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none()
        last_message = None
        if last_msg:
            if last_msg.is_deleted:
                last_message = "🚫 Nachricht gelöscht"
            elif last_msg.msg_type == "text":
                last_message = last_msg.text
            elif last_msg.msg_type == "image":
                if last_msg.external_url or (last_msg.file_name and last_msg.file_name.lower().endswith(".gif")):
                    last_message = "👾 GIF"
                else:
                    last_message = "📷 Bild"
            else:
                last_message = {
                    "file": "📎 Datei", "voice": "🎤 Sprachnachricht", "poll": "📊 Umfrage: " + (last_msg.text or ""),
                }.get(last_msg.msg_type, last_msg.msg_type)

        unread_query = select(func.count()).select_from(ChatMessage).where(
            ChatMessage.conversation_id == conv.id, ChatMessage.sender_id != current_user.id,
        )
        if part.last_read_at:
            unread_query = unread_query.where(ChatMessage.created_at > part.last_read_at)
        unread_count = (await db.execute(unread_query)).scalar() or 0

        out.append(ChatConversationOut(
            id=conv.id, is_group=conv.is_group, name=conv.name, avatar_url=conv.avatar_url,
            is_notes=bool(conv.is_notes), participants=participants,
            last_message=last_message, last_message_at=last_msg.created_at if last_msg else None,
            unread_count=unread_count, is_muted=part.is_muted,
        ))

    out.sort(key=lambda c: (c.is_notes, c.last_message_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return out

@router.post("/conversations", response_model=ChatConversationOut)
async def create_conversation(data: ChatConversationCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        raise HTTPException(400, "Keine Klasse zugewiesen")
    participant_ids = sorted(set(data.participant_ids) - {current_user.id})
    if not participant_ids:
        raise HTTPException(400, "Mindestens eine weitere Person auswählen")

    # Only classmates — never let a conversation include someone outside
    # the requester's own class.
    valid_result = await db.execute(select(User.id).where(
        User.id.in_(participant_ids), User.class_id == current_user.class_id,
    ))
    valid_ids = {row[0] for row in valid_result.all()}
    if valid_ids != set(participant_ids):
        raise HTTPException(400, "Nur Mitschüler:innen auswählbar")

    is_group = data.is_group or len(participant_ids) > 1

    # 1:1 chats are reused instead of duplicated — find an existing
    # non-group conversation with exactly these two participants.
    if not is_group:
        other_id = participant_ids[0]
        candidate_result = await db.execute(
            select(ChatParticipant.conversation_id).where(ChatParticipant.user_id == current_user.id)
            .intersect(select(ChatParticipant.conversation_id).where(ChatParticipant.user_id == other_id))
        )
        for (conv_id,) in candidate_result.all():
            conv_check = await db.execute(select(ChatConversation).where(
                ChatConversation.id == conv_id, ChatConversation.is_group == False,  # noqa: E712
            ))
            existing = conv_check.scalar_one_or_none()
            if existing:
                participants = await _participants_out(db, existing.id)
                return ChatConversationOut(
                    id=existing.id, is_group=False, name=existing.name, participants=participants,
                    last_message=None, last_message_at=None, unread_count=0,
                )

    conv = ChatConversation(
        class_id=current_user.class_id, is_group=is_group,
        name=(data.name or None) if is_group else None, created_by=current_user.id,
    )
    db.add(conv)
    await db.flush()

    for uid in [current_user.id, *participant_ids]:
        db.add(ChatParticipant(conversation_id=conv.id, user_id=uid))
    await db.commit()
    await db.refresh(conv)

    participants = await _participants_out(db, conv.id)
    return ChatConversationOut(
        id=conv.id, is_group=conv.is_group, name=conv.name, participants=participants,
        last_message=None, last_message_at=None, unread_count=0,
    )

@router.get("/conversations/{conversation_id}/messages", response_model=List[ChatMessageOut])
async def list_messages(conversation_id: int, before: Optional[int] = None, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await _require_participant(db, conversation_id, current_user)

    query = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    if before:
        query = query.where(ChatMessage.id < before)
    query = query.order_by(ChatMessage.created_at.desc()).limit(100)
    result = await db.execute(query)
    messages = list(reversed(result.scalars().all()))

    sender_ids = {m.sender_id for m in messages}
    senders = {}
    if sender_ids:
        sender_result = await db.execute(select(User).where(User.id.in_(sender_ids)))
        senders = {u.id: u for u in sender_result.scalars().all()}

    polls = await _polls_out(db, messages, current_user)
    reply_previews = await _reply_previews_out(db, messages)
    reactions = await _reactions_out(db, messages, current_user)

    return [
        ChatMessageOut(
            id=m.id, conversation_id=m.conversation_id, sender_id=m.sender_id,
            sender_name=senders[m.sender_id].name if m.sender_id in senders else "?",
            sender_avatar=senders[m.sender_id].avatar_url if m.sender_id in senders else None,
            msg_type=m.msg_type,
            text=None if m.is_deleted else m.text,
            file_name=None if m.is_deleted else m.file_name,
            file_size=None if m.is_deleted else m.file_size,
            mime_type=None if m.is_deleted else m.mime_type,
            external_url=None if m.is_deleted else m.external_url,
            poll=None if m.is_deleted else polls.get(m.id),
            reply_to=reply_previews.get(m.id),
            reactions=[] if m.is_deleted else reactions.get(m.id, []),
            is_edited=bool(m.is_edited),
            is_deleted=bool(m.is_deleted),
            created_at=m.created_at,
        ) for m in messages
    ]

@router.post("/conversations/{conversation_id}/messages", response_model=ChatMessageOut)
async def send_message(conversation_id: int, data: ChatMessageCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await _require_participant(db, conversation_id, current_user)

    if data.msg_type == "text" and not (data.text or "").strip():
        raise HTTPException(400, "Nachricht darf nicht leer sein")
    if data.msg_type == "poll":
        options = [o.strip() for o in (data.poll_options or []) if o.strip()]
        if not (data.text or "").strip():
            raise HTTPException(400, "Frage darf nicht leer sein")
        if len(options) < 2:
            raise HTTPException(400, "Mindestens 2 Antwortmöglichkeiten angeben")
    elif data.msg_type not in ("text", "poll") and not data.storage_filename and not data.external_url:
        raise HTTPException(400, "Datei fehlt")

    reply_to_id = None
    if data.reply_to_id:
        orig_result = await db.execute(select(ChatMessage).where(
            ChatMessage.id == data.reply_to_id, ChatMessage.conversation_id == conversation_id,
        ))
        if orig_result.scalar_one_or_none():
            reply_to_id = data.reply_to_id

    msg = ChatMessage(
        conversation_id=conversation_id, sender_id=current_user.id, msg_type=data.msg_type,
        text=data.text, storage_filename=data.storage_filename, file_name=data.file_name,
        file_size=data.file_size, mime_type=data.mime_type, reply_to_id=reply_to_id,
        poll_multi=data.poll_multi if data.msg_type == "poll" else False,
        external_url=data.external_url,
    )
    db.add(msg)
    await db.flush()

    poll_out = None
    if data.msg_type == "poll":
        options = [o.strip() for o in (data.poll_options or []) if o.strip()]
        opt_rows = [ChatPollOption(message_id=msg.id, option_text=text, position=i) for i, text in enumerate(options)]
        db.add_all(opt_rows)
        await db.flush()
        poll_out = ChatPollOut(
            question=msg.text or "",
            options=[ChatPollOptionOut(id=o.id, option_text=o.option_text, vote_count=0) for o in opt_rows],
            my_votes=[], allow_multiple=data.poll_multi, total_votes=0,
        )

    await db.commit()
    await db.refresh(msg)

    # Sending a message means you've obviously seen the conversation up to
    # this point — mark it read for the sender too.
    await db.execute(
        ChatParticipant.__table__.update()
        .where(ChatParticipant.conversation_id == conversation_id, ChatParticipant.user_id == current_user.id)
        .values(last_read_at=msg.created_at)
    )
    await db.commit()

    others_result = await db.execute(
        select(User, ChatParticipant.is_muted).join(ChatParticipant, ChatParticipant.user_id == User.id)
        .where(ChatParticipant.conversation_id == conversation_id, ChatParticipant.user_id != current_user.id)
    )
    all_others = others_result.all()
    mentioned_users = []
    normal_unmuted = []
    text_lower = (data.text or "").lower() if data.msg_type == "text" else ""

    for u, muted in all_others:
        u_name = (u.display_name or u.name or "").lower()
        u_first = u_name.split()[0] if u_name else ""
        is_mentioned = False
        if text_lower and u_name and (f"@{u_name}" in text_lower or (u_first and f"@{u_first}" in text_lower)):
            is_mentioned = True

        ns = await get_user_settings(db, u.id)
        if ns.enabled and ns.chat_new:
            if is_mentioned:
                mentioned_users.append(u)
            elif not muted:
                normal_unmuted.append(u)

    conv_info = await db.execute(select(ChatConversation).where(ChatConversation.id == conversation_id))
    conv_row = conv_info.scalar_one_or_none()
    group_prefix = f" [{conv_row.name}]" if conv_row and conv_row.is_group and conv_row.name else ""

    if data.msg_type == "text":
        preview = data.text
    elif data.msg_type == "image" and (data.external_url or (data.file_name and data.file_name.lower().endswith(".gif"))):
        preview = "👾 GIF"
    elif data.msg_type == "image":
        preview = "📷 Bild"
    elif data.msg_type == "file":
        preview = "📎 Datei"
    elif data.msg_type == "voice":
        preview = "🎤 Sprachnachricht"
    elif data.msg_type == "poll":
        preview = "📊 Umfrage: " + (data.text or "")
    else:
        preview = "Neue Nachricht"

    try:
        if mentioned_users:
            await push_to_users(
                db, mentioned_users,
                title=f"{current_user.name}{group_prefix} hat dich erwähnt",
                body=preview or "Erwähnung in einer Nachricht",
                tag=f"chat-{conversation_id}",
                url="/chat",
            )
        if normal_unmuted:
            await push_to_users(
                db, normal_unmuted,
                title=f"{current_user.name}{group_prefix}",
                body=preview or "Neue Nachricht",
                tag=f"chat-{conversation_id}",
                url="/chat",
            )
    except Exception as e:
        logger.warning("Chat push notification failed: %s", e)

    reply_out = None
    if msg.reply_to_id:
        previews = await _reply_previews_out(db, [msg])
        reply_out = previews.get(msg.id)

    return ChatMessageOut(
        id=msg.id, conversation_id=msg.conversation_id, sender_id=msg.sender_id,
        sender_name=current_user.name, sender_avatar=current_user.avatar_url,
        msg_type=msg.msg_type, text=msg.text,
        file_name=msg.file_name, file_size=msg.file_size, mime_type=msg.mime_type, external_url=msg.external_url,
        poll=poll_out, reply_to=reply_out, is_edited=False, is_deleted=False, created_at=msg.created_at,
    )

@router.patch("/conversations/{conversation_id}/messages/{message_id}", response_model=ChatMessageOut)
async def edit_message(conversation_id: int, message_id: int, data: ChatMessageUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await _require_participant(db, conversation_id, current_user)
    msg_result = await db.execute(select(ChatMessage).where(
        ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id,
    ))
    msg = msg_result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404)
    if msg.sender_id != current_user.id:
        raise HTTPException(403, "Nur eigene Nachrichten können bearbeitet werden")
    if msg.is_deleted:
        raise HTTPException(400, "Gelöschte Nachrichten können nicht bearbeitet werden")
    if msg.msg_type != "text":
        raise HTTPException(400, "Nur Textnachrichten können bearbeitet werden")
    new_text = (data.text or "").strip()
    if not new_text:
        raise HTTPException(400, "Nachricht darf nicht leer sein")
    msg.text = new_text
    msg.is_edited = True
    await db.commit()
    await db.refresh(msg)

    reply_out = None
    if msg.reply_to_id:
        previews = await _reply_previews_out(db, [msg])
        reply_out = previews.get(msg.id)
    reactions = await _reactions_out(db, [msg], current_user)

    return ChatMessageOut(
        id=msg.id, conversation_id=msg.conversation_id, sender_id=msg.sender_id,
        sender_name=current_user.name, sender_avatar=current_user.avatar_url,
        msg_type=msg.msg_type, text=msg.text,
        file_name=msg.file_name, file_size=msg.file_size, mime_type=msg.mime_type, external_url=msg.external_url,
        poll=None, reply_to=reply_out, reactions=reactions.get(msg.id, []),
        is_edited=True, is_deleted=False, created_at=msg.created_at,
    )

@router.delete("/conversations/{conversation_id}/messages/{message_id}")
async def delete_message(conversation_id: int, message_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    conv = await _require_participant(db, conversation_id, current_user)
    msg_result = await db.execute(select(ChatMessage).where(
        ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id,
    ))
    msg = msg_result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404)
    if msg.sender_id != current_user.id and not conv.is_notes:
        raise HTTPException(403, "Nur eigene Nachrichten können gelöscht werden")

    if msg.storage_filename:
        path = os.path.join(settings.chat_storage_dir, msg.storage_filename)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        msg.storage_filename = None

    msg.is_deleted = True
    msg.text = None
    msg.file_name = None
    msg.file_size = None
    msg.mime_type = None
    msg.external_url = None
    await db.commit()
    return {"ok": True}

@router.patch("/conversations/{conversation_id}", response_model=ChatConversationOut)
async def update_conversation(conversation_id: int, data: ChatGroupUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    conv = await _require_participant(db, conversation_id, current_user)
    if not conv.is_group:
        raise HTTPException(400, "Nur Gruppen können bearbeitet werden")
    if data.name is not None:
        new_name = data.name.strip()
        if not new_name:
            raise HTTPException(400, "Gruppenname darf nicht leer sein")
        conv.name = new_name
    if data.avatar_url is not None:
        conv.avatar_url = data.avatar_url
    await db.commit()
    await db.refresh(conv)
    participants = await _participants_out(db, conv.id)
    return ChatConversationOut(
        id=conv.id, is_group=conv.is_group, name=conv.name, avatar_url=conv.avatar_url,
        is_notes=bool(conv.is_notes), participants=participants,
    )

@router.post("/conversations/{conversation_id}/avatar", response_model=ChatConversationOut)
async def upload_group_avatar(conversation_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    conv = await _require_participant(db, conversation_id, current_user)
    if not conv.is_group:
        raise HTTPException(400, "Nur für Gruppenchats")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Nur Bilddateien erlaubt")
    raw = await file.read()
    if len(raw) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")
    await scan_file(raw, file.filename or "group_avatar.webp", file.content_type)
    try:
        processed = _compress_avatar(raw)
    except Exception:
        raise HTTPException(400, "Ungültiges Bild")

    avatar_dir = os.path.join(settings.upload_dir, AVATAR_DIR_NAME)
    os.makedirs(avatar_dir, exist_ok=True)
    filename = f"group_{uuid.uuid4().hex}.webp"
    async with aiofiles.open(os.path.join(avatar_dir, filename), "wb") as out:
        await out.write(processed)

    conv.avatar_url = f"/uploads/{AVATAR_DIR_NAME}/{filename}"
    await db.commit()
    await db.refresh(conv)
    participants = await _participants_out(db, conv.id)
    return ChatConversationOut(
        id=conv.id, is_group=conv.is_group, name=conv.name, avatar_url=conv.avatar_url,
        is_notes=bool(conv.is_notes), participants=participants,
    )

@router.post("/conversations/{conversation_id}/messages/{message_id}/vote", response_model=ChatPollOut)
async def vote_poll(conversation_id: int, message_id: int, data: ChatVoteCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await _require_participant(db, conversation_id, current_user)

    msg_result = await db.execute(select(ChatMessage).where(
        ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id, ChatMessage.msg_type == "poll",
    ))
    msg = msg_result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404)

    opt_result = await db.execute(select(ChatPollOption).where(
        ChatPollOption.id == data.option_id, ChatPollOption.message_id == message_id,
    ))
    if not opt_result.scalar_one_or_none():
        raise HTTPException(400, "Ungültige Option")

    if msg.poll_multi:
        # Multi-choice: tapping an option toggles just that one — add it if
        # not yet selected, remove it if it already was — leaving the
        # user's other selections on this poll untouched.
        existing_result = await db.execute(select(ChatPollVote).where(
            ChatPollVote.message_id == message_id, ChatPollVote.option_id == data.option_id,
            ChatPollVote.user_id == current_user.id,
        ))
        existing = existing_result.scalar_one_or_none()
        if existing:
            await db.delete(existing)
        else:
            db.add(ChatPollVote(message_id=message_id, option_id=data.option_id, user_id=current_user.id))
    else:
        # Single-choice: replace any existing vote by this user on this poll
        # rather than adding a second one.
        await db.execute(
            ChatPollVote.__table__.delete().where(ChatPollVote.message_id == message_id, ChatPollVote.user_id == current_user.id)
        )
        db.add(ChatPollVote(message_id=message_id, option_id=data.option_id, user_id=current_user.id))
    await db.commit()

    polls = await _polls_out(db, [msg], current_user)
    return polls[msg.id]

@router.post("/conversations/{conversation_id}/read")
async def mark_read(conversation_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await _require_participant(db, conversation_id, current_user)
    await db.execute(
        ChatParticipant.__table__.update()
        .where(ChatParticipant.conversation_id == conversation_id, ChatParticipant.user_id == current_user.id)
        .values(last_read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"ok": True}

@router.post("/conversations/{conversation_id}/mute")
async def set_mute(conversation_id: int, data: ChatMuteUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Muting only ever affects the caller's own push notifications for
    this one conversation — a per-participant setting, not something that
    touches anyone else or any other chat."""
    await _require_participant(db, conversation_id, current_user)
    await db.execute(
        ChatParticipant.__table__.update()
        .where(ChatParticipant.conversation_id == conversation_id, ChatParticipant.user_id == current_user.id)
        .values(is_muted=data.muted)
    )
    await db.commit()
    return {"ok": True, "muted": data.muted}

@router.post("/conversations/{conversation_id}/messages/{message_id}/react", response_model=List[ChatReactionOut])
async def react_to_message(conversation_id: int, message_id: int, data: ChatReactionCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Single reaction per user per message — tapping the same emoji again
    removes it (toggle off), tapping a different one swaps it, same
    single-choice pattern the poll vote already uses."""
    await _require_participant(db, conversation_id, current_user)

    msg_result = await db.execute(select(ChatMessage).where(
        ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id,
    ))
    msg = msg_result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404)

    existing_result = await db.execute(select(ChatMessageReaction).where(
        ChatMessageReaction.message_id == message_id, ChatMessageReaction.user_id == current_user.id,
    ))
    existing = existing_result.scalar_one_or_none()

    if existing and existing.emoji == data.emoji:
        await db.delete(existing)
    elif existing:
        existing.emoji = data.emoji
    else:
        db.add(ChatMessageReaction(message_id=message_id, user_id=current_user.id, emoji=data.emoji))
    await db.commit()

    reactions = await _reactions_out(db, [msg], current_user)
    return reactions.get(message_id, [])

CHAT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

def _chat_ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()

@router.post("/conversations/{conversation_id}/upload")
async def upload_chat_file(conversation_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Uploads immediately, before the message itself is sent — the client
    references the returned storage_filename in a follow-up POST .../messages
    call, same two-step flow homework's solution attachments and Drive
    already use."""
    await _require_participant(db, conversation_id, current_user)

    content = await file.read()
    if len(content) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")
    await scan_file(content, file.filename or "unknown", file.content_type)

    ext = _chat_ext(file.filename or "")
    storage_filename = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(settings.chat_storage_dir, exist_ok=True)
    dest = os.path.join(settings.chat_storage_dir, storage_filename)
    async with aiofiles.open(dest, "wb") as out:
        await out.write(content)

    is_image = ext in CHAT_IMAGE_EXTS or (file.content_type or "").startswith("image/")
    return {
        "storage_filename": storage_filename,
        "file_name": file.filename or "Datei",
        "file_size": len(content),
        "mime_type": file.content_type,
        "msg_type": "image" if is_image else "file",
    }

@router.get("/gifs")
async def search_gifs(q: str = "", current_user: User = Depends(get_current_user)):
    """Proxies GIPHY so the API key never reaches the client — not
    conversation-scoped (no _require_participant) since browsing GIFs isn't
    conversation-specific, only actually sending one is (that still goes
    through the normal participant-checked POST .../messages). Empty q
    returns GIPHY's trending feed instead of a search."""
    if not settings.giphy_api_key:
        raise HTTPException(503, "GIF-Suche nicht konfiguriert")

    q = q.strip()
    base = "https://api.giphy.com/v1/gifs/search" if q else "https://api.giphy.com/v1/gifs/trending"
    params = {"api_key": settings.giphy_api_key, "limit": 24, "rating": "g"}
    if q:
        params["q"] = q

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(base, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("GIPHY request failed: %s", e)
        raise HTTPException(502, "GIF-Suche momentan nicht erreichbar")

    results = []
    for item in data.get("data", []):
        images = item.get("images", {})
        # preview_gif for the grid — GIPHY's rendition made specifically for
        # fast preview loading. fixed_height_small ("small" only in pixel
        # size, not necessarily frame count/complexity) was measured up to
        # 516KB for a single 100px-tall thumbnail on some GIFs, while
        # preview_gif stayed a consistent ~25-50KB across a real search —
        # loading 24 of the former at once is what made the grid crawl.
        # fixed_height (200px, GIPHY's own recommended "present after
        # selection" size) matches the bubble's own max-height:280px
        # display cap almost exactly, so nothing is lost by not sending the
        # full original.
        preview = images.get("preview_gif") or images.get("fixed_height_small") or images.get("fixed_height") or {}
        send = images.get("fixed_height") or images.get("original") or preview
        if not send.get("url"):
            continue
        results.append({
            "id": item.get("id"),
            "preview_url": preview.get("url") or send.get("url"),
            "gif_url": send.get("url"),
            "width": int(preview.get("width") or 0),
            "height": int(preview.get("height") or 0),
        })
    return {"results": results}

@router.get("/conversations/{conversation_id}/messages/{message_id}/file")
async def get_chat_file(conversation_id: int, message_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await _require_participant(db, conversation_id, current_user)
    msg_result = await db.execute(select(ChatMessage).where(
        ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id,
    ))
    msg = msg_result.scalar_one_or_none()
    if not msg or not msg.storage_filename:
        raise HTTPException(404)
    path = os.path.join(settings.chat_storage_dir, msg.storage_filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    # Accept-Ranges tells the browser up front that this URL supports byte
    # ranges — without it, a voice message's <audio> element sees an empty
    # seekable range even once duration is known, so scrubbing anywhere
    # snaps straight back to 0 instead of actually seeking.
    range_headers = {"Accept-Ranges": "bytes"}
    if msg.msg_type == "file":
        return FileResponse(path, filename=msg.file_name, media_type=msg.mime_type or "application/octet-stream", headers=range_headers)
    return FileResponse(path, media_type=msg.mime_type or "application/octet-stream", content_disposition_type="inline", headers=range_headers)
