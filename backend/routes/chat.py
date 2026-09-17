from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.chat_conversation import ChatConversation
from backend.models.chat_participant import ChatParticipant
from backend.models.chat_message import ChatMessage
from backend.models.chat_poll_option import ChatPollOption
from backend.models.chat_poll_vote import ChatPollVote
from backend.models.user import User
from backend.schemas import (
    ChatConversationOut, ChatConversationCreate, ChatParticipantOut, ChatMessageOut, ChatMessageCreate,
    ChatMuteUpdate, ChatPollOptionOut, ChatPollOut, ChatVoteCreate,
)
from backend.routes.vapid import push_to_users
from datetime import datetime, timezone
from typing import List, Optional
import logging

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
    return [ChatParticipantOut(user_id=u.id, display_name=u.name) for u in result.scalars().all()]

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
    my_votes: dict[int, int] = {}
    for v in votes:
        counts[v.option_id] = counts.get(v.option_id, 0) + 1
        if v.user_id == current_user.id:
            my_votes[v.message_id] = v.option_id

    out = {}
    for msg in messages:
        if msg.msg_type != "poll":
            continue
        opts = options_by_msg.get(msg.id, [])
        total = sum(counts.get(o.id, 0) for o in opts)
        out[msg.id] = ChatPollOut(
            question=msg.text or "",
            options=[ChatPollOptionOut(id=o.id, option_text=o.option_text, vote_count=counts.get(o.id, 0)) for o in opts],
            my_vote=my_votes.get(msg.id),
            total_votes=total,
        )
    return out

@router.get("/conversations", response_model=List[ChatConversationOut])
async def list_conversations(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
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
            last_message = last_msg.text if last_msg.msg_type == "text" else {
                "image": "📷 Bild", "file": "📎 Datei", "voice": "🎤 Sprachnachricht", "poll": "📊 Umfrage: " + (last_msg.text or ""),
            }.get(last_msg.msg_type, last_msg.msg_type)

        unread_query = select(func.count()).select_from(ChatMessage).where(
            ChatMessage.conversation_id == conv.id, ChatMessage.sender_id != current_user.id,
        )
        if part.last_read_at:
            unread_query = unread_query.where(ChatMessage.created_at > part.last_read_at)
        unread_count = (await db.execute(unread_query)).scalar() or 0

        out.append(ChatConversationOut(
            id=conv.id, is_group=conv.is_group, name=conv.name, participants=participants,
            last_message=last_message, last_message_at=last_msg.created_at if last_msg else None,
            unread_count=unread_count, is_muted=part.is_muted,
        ))

    out.sort(key=lambda c: c.last_message_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
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
        senders = {u.id: u.name for u in sender_result.scalars().all()}

    polls = await _polls_out(db, messages, current_user)

    return [
        ChatMessageOut(
            id=m.id, conversation_id=m.conversation_id, sender_id=m.sender_id,
            sender_name=senders.get(m.sender_id, "?"), msg_type=m.msg_type, text=m.text,
            file_name=m.file_name, file_size=m.file_size, mime_type=m.mime_type,
            poll=polls.get(m.id), created_at=m.created_at,
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
    elif data.msg_type not in ("text", "poll") and not data.storage_filename:
        raise HTTPException(400, "Datei fehlt")

    msg = ChatMessage(
        conversation_id=conversation_id, sender_id=current_user.id, msg_type=data.msg_type,
        text=data.text, storage_filename=data.storage_filename, file_name=data.file_name,
        file_size=data.file_size, mime_type=data.mime_type,
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
            my_vote=None, total_votes=0,
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
    unmuted_others = [u for u, muted in others_result.all() if not muted]
    if unmuted_others:
        preview = data.text if data.msg_type == "text" else {
            "image": "📷 Bild", "file": "📎 Datei", "voice": "🎤 Sprachnachricht", "poll": "📊 Umfrage: " + (data.text or ""),
        }.get(data.msg_type, "Neue Nachricht")
        try:
            await push_to_users(db, unmuted_others, title=current_user.name, body=preview or "Neue Nachricht")
        except Exception as e:
            logger.warning("Chat push notification failed: %s", e)

    return ChatMessageOut(
        id=msg.id, conversation_id=msg.conversation_id, sender_id=msg.sender_id,
        sender_name=current_user.name, msg_type=msg.msg_type, text=msg.text,
        file_name=msg.file_name, file_size=msg.file_size, mime_type=msg.mime_type,
        poll=poll_out, created_at=msg.created_at,
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
