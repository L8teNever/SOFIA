from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import require_super_admin, require_admin
from backend.models.user import User
from backend.models.class_group import ClassGroup
from backend.models.subject import Subject
from backend.schemas import UserOut, ClassGroupOut
from typing import List

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

@router.get("/stats")
async def admin_stats(db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    users = (await db.execute(select(User))).scalars().all()
    classes = (await db.execute(select(ClassGroup))).scalars().all()
    return {"users": len(users), "classes": len(classes)}

@router.get("/users", response_model=List[UserOut])
async def all_users(db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    result = await db.execute(select(User))
    return result.scalars().all()

@router.get("/class-users", response_model=List[UserOut])
async def class_users(db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    result = await db.execute(select(User).where(User.class_id == current_user.class_id))
    return result.scalars().all()
