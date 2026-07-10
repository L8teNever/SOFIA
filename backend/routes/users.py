from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_super_admin
from backend.models.user import User, UserRole
from backend.schemas import UserOut, UserUpdate, UserAdminUpdate, UserCreate
from backend.config import settings
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

@router.post("/register")
async def register_self(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"status": "already_registered", "user": UserOut.model_validate(current_user)}

@router.post("/me/avatar", response_model=UserOut)
async def upload_avatar(file: UploadFile = File(...), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Nur Bilddateien erlaubt")
    raw = await file.read()
    if len(raw) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß")
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
    return current_user

@router.delete("/me/avatar", response_model=UserOut)
async def delete_avatar(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    _delete_avatar_file(current_user.avatar_url)
    current_user.avatar_url = None
    await db.commit()
    await db.refresh(current_user)
    return current_user
