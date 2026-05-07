from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_super_admin, require_admin
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.schemas import ClassGroupOut, ClassGroupCreate
from backend.config import settings
from cryptography.fernet import Fernet
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/classes", tags=["classes"])

def get_fernet():
    key = settings.encryption_key
    if not key:
        return None
    return Fernet(key.encode())

@router.get("/", response_model=List[ClassGroupOut])
async def list_classes(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    result = await db.execute(select(ClassGroup))
    return result.scalars().all()

@router.post("/", response_model=ClassGroupOut)
async def create_class(data: ClassGroupCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    cls = ClassGroup(name=data.name)
    db.add(cls)
    await db.commit()
    await db.refresh(cls)
    return cls

@router.delete("/{class_id}")
async def delete_class(class_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404, "Class not found")
    await db.delete(cls)
    await db.commit()
    return {"ok": True}

class UntisCredentials(BaseModel):
    url: str
    school: str
    class_name: str
    username: str
    password: str

@router.post("/{class_id}/untis")
async def save_untis(class_id: int, data: UntisCredentials, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    if current_user.role != "super_admin" and current_user.class_id != class_id:
        raise HTTPException(403, "Not your class")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    f = get_fernet()
    cls.untis_url = data.url
    cls.untis_school = data.school
    cls.untis_class = data.class_name
    cls.untis_user = data.username
    cls.untis_password_enc = f.encrypt(data.password.encode()).decode() if f else data.password
    await db.commit()
    return {"ok": True}

@router.get("/{class_id}/untis")
async def get_untis_status(class_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    return {
        "configured": bool(cls.untis_url),
        "url": cls.untis_url,
        "school": cls.untis_school,
        "class_name": cls.untis_class,
        "username": cls.untis_user,
    }
