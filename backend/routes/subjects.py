from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from backend.database import get_db
from backend.auth import get_current_user, require_admin
from backend.models.subject import Subject
from backend.models.user import User
from backend.schemas import SubjectOut, SubjectCreate
from typing import List

router = APIRouter(prefix="/api/v1/subjects", tags=["subjects"])

@router.get("/", response_model=List[SubjectOut])
async def list_subjects(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Subject).where(or_(Subject.class_id == current_user.class_id, Subject.is_global == True))
    )
    return result.scalars().all()

@router.post("/", response_model=SubjectOut)
async def create_subject(data: SubjectCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    class_id = None if data.is_global else current_user.class_id
    subject = Subject(
        name=data.name,
        short_name=data.short_name,
        color=data.color,
        class_id=class_id,
        is_global=data.is_global,
    )
    db.add(subject)
    await db.commit()
    await db.refresh(subject)
    return subject

@router.delete("/{subject_id}")
async def delete_subject(subject_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    result = await db.execute(select(Subject).where(Subject.id == subject_id))
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(404)
    if not subject.is_global and subject.class_id != current_user.class_id:
        raise HTTPException(403)
    await db.delete(subject)
    await db.commit()
    return {"ok": True}
