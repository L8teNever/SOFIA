from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.grade import Grade
from backend.models.user import User
from backend.schemas import GradeOut, GradeCreate
from typing import List

router = APIRouter(prefix="/api/v1/grades", tags=["grades"])

@router.get("/", response_model=List[GradeOut])
async def list_grades(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Grade).where(Grade.user_id == current_user.id).order_by(Grade.date.desc()))
    return result.scalars().all()

@router.post("/", response_model=GradeOut)
async def create_grade(data: GradeCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    grade = Grade(user_id=current_user.id, **data.model_dump())
    db.add(grade)
    await db.commit()
    await db.refresh(grade)
    return grade

@router.delete("/{grade_id}")
async def delete_grade(grade_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Grade).where(Grade.id == grade_id, Grade.user_id == current_user.id))
    grade = result.scalar_one_or_none()
    if not grade:
        raise HTTPException(404)
    await db.delete(grade)
    await db.commit()
    return {"ok": True}
