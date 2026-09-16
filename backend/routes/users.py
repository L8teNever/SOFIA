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
async def update_user(user_id: int, data: UserAdminUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        user.role = UserRole(data.role)
    if data.class_id is not None:
        user.class_id = data.class_id
    await db.commit()
    await db.refresh(user)
    return user

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
