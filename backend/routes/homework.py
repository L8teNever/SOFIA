from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.homework import Homework
from backend.models.user import User
from backend.schemas import HomeworkOut, HomeworkCreate
from backend.config import settings
from typing import List
import os, uuid, aiofiles

router = APIRouter(prefix="/api/v1/homework", tags=["homework"])

@router.get("/", response_model=List[HomeworkOut])
async def list_homework(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Homework).where(Homework.class_id == current_user.class_id).order_by(Homework.due_date)
    )
    return result.scalars().all()

@router.post("/upload")
async def upload_homework_file(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    if file.size and file.size > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß")
    ext = os.path.splitext(file.filename or "")[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    hw_dir = os.path.join(settings.upload_dir, "homework")
    os.makedirs(hw_dir, exist_ok=True)
    async with aiofiles.open(os.path.join(hw_dir, filename), "wb") as out:
        await out.write(await file.read())
    file_type = "image" if (file.content_type or "").startswith("image/") else "file"
    return {"url": f"/uploads/homework/{filename}", "type": file_type, "name": file.filename}

@router.post("/", response_model=HomeworkOut)
async def create_homework(data: HomeworkCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    hw = Homework(
        subject_id=data.subject_id,
        class_id=current_user.class_id,
        description=data.description,
        due_date=data.due_date,
        created_by=current_user.id,
        checked_by=[],
        file_url=data.file_url,
        file_type=data.file_type,
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
