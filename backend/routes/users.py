from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_super_admin
from backend.models.user import User, UserRole
from backend.models.user_email_alias import UserEmailAlias
from backend.schemas import UserOut, UserUpdate, UserAdminUpdate, UserCreate, EmailAliasOut, EmailAliasCreate
from backend.config import settings
from backend.services.virus_scanner import scan_file
from backend.services.audit_service import log_audit
from typing import List
from PIL import Image, ImageOps
import aiofiles, uuid, os, io

router = APIRouter(prefix="/api/v1/users", tags=["users"])

AVATAR_SIZE = 320
AVATAR_DIR_NAME = "avatars"

def _compress_avatar(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    if side > AVATAR_SIZE:
        img = img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=80, method=6)
    return out.getvalue()

def _delete_avatar_file(avatar_url: str | None):
    if not avatar_url:
        return
    path = os.path.join(settings.upload_dir, AVATAR_DIR_NAME, os.path.basename(avatar_url))
    if os.path.exists(path):
        os.remove(path)

@router.get("/", response_model=List[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    result = await db.execute(select(User))
    return result.scalars().all()

@router.post("/", response_model=UserOut)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    email = data.email.strip().lower()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "E-Mail bereits vorhanden")
    user = User(email=email, display_name=data.display_name or None, role=UserRole(data.role), class_id=data.class_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.get("/class", response_model=List[UserOut])
async def list_class_users(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        return []
    result = await db.execute(select(User).where(User.class_id == current_user.class_id))
    return result.scalars().all()

@router.patch("/me", response_model=UserOut)
async def update_me(data: UserUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if data.display_name is not None:
        current_user.display_name = data.display_name
    await db.commit()
    await db.refresh(current_user)
    return current_user

@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, data: UserAdminUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    sent = data.model_fields_set
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        if user_id == current_user.id and data.role != "super_admin":
            raise HTTPException(400, "Du kannst dir die Super-Admin-Rolle nicht selbst nehmen")
        user.role = UserRole(data.role)
    if "class_id" in sent:
        user.class_id = data.class_id  # null = keine Klasse
    if data.is_active is not None:
        if user_id == current_user.id and not data.is_active:
            raise HTTPException(400, "Du kannst dich nicht selbst sperren")
        user.is_active = data.is_active
    await db.commit()
    await db.refresh(user)
    return user

@router.delete("/{user_id}")
async def delete_user(user_id: int, request: Request, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    """Nutzer endgueltig loeschen (nur Super-Admin).

    Weg sind seine persoenlichen Daten: Noten, eigene Loesungen, persoenliche Termine,
    Push/Benachrichtigungen, Google-Verbindung, weitere E-Mail-Adressen, geteilte Dateien,
    Chat-Mitgliedschaften, Reaktionen und Abstimmungen. Was er fuer die Klasse angelegt
    hat (Hausaufgaben, Klassentermine, Chat-Nachrichten, Essensplaene), bleibt stehen.
    Meldet sich die Adresse spaeter wieder an, gibt es kein Konto mehr (403).
    """
    from sqlalchemy import delete as sql_delete
    from backend.models.grade import Grade
    from backend.models.homework import Homework
    from backend.models.homework_solution import HomeworkSolution
    from backend.models.calendar_event import CalendarEvent
    from backend.models.calendar_category import CalendarCategory
    from backend.models.shared_file import SharedFile
    from backend.models.push_subscription import PushSubscription
    from backend.models.notification import Notification
    from backend.models.notification_setting import UserNotificationSetting
    from backend.models.google_account import GoogleAccount
    from backend.models.google_sync_map import GoogleSyncedEvent, GoogleSyncedTask
    from backend.models.chat_participant import ChatParticipant
    from backend.models.chat_message_reaction import ChatMessageReaction
    from backend.models.chat_poll_vote import ChatPollVote

    if user_id == current_user.id:
        raise HTTPException(400, "Du kannst dich nicht selbst löschen")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.role == UserRole.super_admin:
        raise HTTPException(400, "Super-Admins erst zu Admin oder Schüler machen, dann löschen")

    email = user.email
    # Dateien auf der Platte: geteilte Dateien und Profilbild
    files = (await db.execute(select(SharedFile).where(SharedFile.uploader_id == user_id))).scalars().all()
    for sf in files:
        path = os.path.join(settings.upload_dir, sf.filename)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    try:
        _delete_avatar_file(user.avatar_url)
    except OSError:
        pass

    for model, col in (
        (Grade, Grade.user_id),
        (HomeworkSolution, HomeworkSolution.user_id),
        (SharedFile, SharedFile.uploader_id),
        (PushSubscription, PushSubscription.user_id),
        (Notification, Notification.user_id),
        (UserNotificationSetting, UserNotificationSetting.user_id),
        (GoogleSyncedEvent, GoogleSyncedEvent.user_id),
        (GoogleSyncedTask, GoogleSyncedTask.user_id),
        (GoogleAccount, GoogleAccount.user_id),
        (UserEmailAlias, UserEmailAlias.user_id),
        (ChatMessageReaction, ChatMessageReaction.user_id),
        (ChatPollVote, ChatPollVote.user_id),
        (ChatParticipant, ChatParticipant.user_id),
    ):
        await db.execute(sql_delete(model).where(col == user_id))
    # persoenliche Termine gehoeren nur ihm
    await db.execute(sql_delete(CalendarEvent).where(CalendarEvent.created_by == user_id, CalendarEvent.event_type == "personal"))
    # aus Listen austragen: abgehakte Hausaufgaben, Mitglied in eigenen Kalender-Kategorien
    for hw in (await db.execute(select(Homework))).scalars().all():
        if user_id in (hw.checked_by or []):
            hw.checked_by = [i for i in hw.checked_by if i != user_id]
    for cat in (await db.execute(select(CalendarCategory))).scalars().all():
        if user_id in (cat.member_ids or []):
            cat.member_ids = [i for i in cat.member_ids if i != user_id]
    await db.execute(sql_delete(User).where(User.id == user_id))
    await db.commit()
    await log_audit(db, action="user.delete", user=current_user, entity_type="user", entity_id=user_id,
                    details={"email": email}, request=request)
    return {"ok": True}


@router.get("/{user_id}/emails", response_model=List[EmailAliasOut])
async def list_user_emails(user_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    result = await db.execute(select(UserEmailAlias).where(UserEmailAlias.user_id == user_id))
    return result.scalars().all()

@router.post("/{user_id}/emails", response_model=EmailAliasOut)
async def add_user_email(user_id: int, data: EmailAliasCreate, request: Request, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    email = data.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Ungültige E-Mail-Adresse")

    target = await db.execute(select(User).where(User.id == user_id))
    if not target.scalar_one_or_none():
        raise HTTPException(404, "User not found")

    # Must not collide with anyone's primary email or an existing alias —
    # otherwise two accounts could end up resolving to the same address.
    existing_user = await db.execute(select(User).where(User.email == email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(400, "E-Mail gehört bereits zu einem Hauptaccount")
    existing_alias = await db.execute(select(UserEmailAlias).where(UserEmailAlias.email == email))
    if existing_alias.scalar_one_or_none():
        raise HTTPException(400, "E-Mail ist bereits einem Nutzer zugeordnet")

    alias = UserEmailAlias(user_id=user_id, email=email)
    db.add(alias)
    await db.commit()
    await db.refresh(alias)

    await log_audit(
        db, action="user.email_alias_add", user=current_user,
        entity_type="user", entity_id=user_id, details={"email": email}, request=request,
    )
    return alias

@router.delete("/{user_id}/emails/{alias_id}")
async def delete_user_email(user_id: int, alias_id: int, request: Request, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    result = await db.execute(select(UserEmailAlias).where(UserEmailAlias.id == alias_id, UserEmailAlias.user_id == user_id))
    alias = result.scalar_one_or_none()
    if not alias:
        raise HTTPException(404, "Alias not found")

    await db.delete(alias)
    await db.commit()

    await log_audit(
        db, action="user.email_alias_delete", user=current_user,
        entity_type="user", entity_id=user_id, details={"email": alias.email}, request=request,
    )
    return {"ok": True}

@router.post("/register")
async def register_self(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"status": "already_registered", "user": UserOut.model_validate(current_user)}

@router.post("/me/avatar", response_model=UserOut)
async def upload_avatar(request: Request, file: UploadFile = File(...), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Nur Bilddateien erlaubt")
    raw = await file.read()
    if len(raw) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")

    # 1. Virenscanner
    await scan_file(raw, file.filename or "avatar.webp", file.content_type)

    try:
        processed = _compress_avatar(raw)
    except Exception:
        raise HTTPException(400, "Ungültiges Bild")

    avatar_dir = os.path.join(settings.upload_dir, AVATAR_DIR_NAME)
    os.makedirs(avatar_dir, exist_ok=True)
    _delete_avatar_file(current_user.avatar_url)

    filename = f"{uuid.uuid4().hex}.webp"
    async with aiofiles.open(os.path.join(avatar_dir, filename), "wb") as out:
        await out.write(processed)

    current_user.avatar_url = f"/uploads/{AVATAR_DIR_NAME}/{filename}"
    await db.commit()
    await db.refresh(current_user)

    await log_audit(
        db,
        action="user.avatar_upload",
        user=current_user,
        entity_type="user",
        entity_id=current_user.id,
        details={"filename": filename, "size": len(processed)},
        request=request,
    )

    return current_user

@router.delete("/me/avatar", response_model=UserOut)
async def delete_avatar(request: Request, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    _delete_avatar_file(current_user.avatar_url)
    current_user.avatar_url = None
    await db.commit()
    await db.refresh(current_user)

    await log_audit(
        db,
        action="user.avatar_delete",
        user=current_user,
        entity_type="user",
        entity_id=current_user.id,
        request=request,
    )

    return current_user
