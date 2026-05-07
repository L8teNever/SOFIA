from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.homework import Homework
from backend.models.user import User
from backend.schemas import HomeworkOut, HomeworkCreate
from typing import List

router = APIRouter(prefix="/api/v1/homework", tags=["homework"])

@router.get("/", response_model=List[HomeworkOut])
async def list_homework(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Homework).where(Homework.class_id == current_user.class_id).order_by(Homework.due_date)
    )
    return result.scalars().all()

@router.post("/", response_model=HomeworkOut)
async def create_homework(data: HomeworkCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    hw = Homework(
        subject_id=data.subject_id,
        class_id=current_user.class_id,
        description=data.description,
        due_date=data.due_date,
        created_by=current_user.id,
        checked_by=[],
    )
    db.add(hw)
    await db.commit()
    await db.refresh(hw)
    return hw

@router.post("/{hw_id}/check")
async def toggle_check(hw_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Homework).where(Homework.id == hw_id))
    hw = result.scalar_one_or_none()
    if not hw or hw.class_id != current_user.class_id:
        raise HTTPException(404)
    checked = list(hw.checked_by or [])
    if current_user.id in checked:
        checked.remove(current_user.id)
    else:
        checked.append(current_user.id)
    hw.checked_by = checked
    await db.commit()
    return {"checked": current_user.id in checked}

@router.delete("/{hw_id}")
async def delete_homework(hw_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Homework).where(Homework.id == hw_id))
    hw = result.scalar_one_or_none()
    if not hw or hw.class_id != current_user.class_id:
        raise HTTPException(404)
    if hw.created_by != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)
    await db.delete(hw)
    await db.commit()
    return {"ok": True}
