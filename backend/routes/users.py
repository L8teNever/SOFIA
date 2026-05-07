from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_super_admin
from backend.models.user import User, UserRole
from backend.schemas import UserOut, UserUpdate, UserAdminUpdate
from typing import List

router = APIRouter(prefix="/api/v1/users", tags=["users"])

@router.get("/", response_model=List[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    result = await db.execute(select(User))
    return result.scalars().all()

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
