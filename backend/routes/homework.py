from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.homework import Homework
from backend.models.homework_solution import HomeworkSolution
from backend.models.user import User
from backend.schemas import (
    HomeworkOut, HomeworkCreate, HomeworkUpdate,
    HomeworkSolutionOut, HomeworkSolutionCreate
)
from backend.config import settings
from backend.services.virus_scanner import scan_file
from backend.services.compression import compress_lossless
from backend.services.audit_service import log_audit
from typing import List, Optional
import os, uuid, aiofiles, logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/homework", tags=["homework"])


def _normalize_hw_attachments(hw: Homework):
    if (not hw.attachments or len(hw.attachments) == 0) and hw.file_url:
        hw.attachments = [{"url": hw.file_url, "type": hw.file_type or "file", "name": "Anhang"}]


@router.get("/", response_model=List[HomeworkOut])
async def list_homework(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Homework)
        .where(Homework.class_id == current_user.class_id)
        .order_by(Homework.due_date)
    )
    items = result.scalars().all()
    for hw in items:
        _normalize_hw_attachments(hw)
    return items


@router.get("/{hw_id}", response_model=HomeworkOut)
async def get_homework(hw_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Homework).where(Homework.id == hw_id))
    hw = result.scalar_one_or_none()
    if not hw or hw.class_id != current_user.class_id:
        raise HTTPException(404, "Hausaufgabe nicht gefunden")
    _normalize_hw_attachments(hw)
    return hw


@router.post("/upload")
async def upload_homework_file(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    content = await file.read()
    if len(content) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")

    # 1. Virenscanner
    await scan_file(content, file.filename or "unknown", file.content_type)

    # 2. Verlustfreie Komprimierung
    content, ext, mime = compress_lossless(content, file.filename or "", file.content_type)
    if not ext:
        ext = os.path.splitext(file.filename or "")[1]

    filename = f"{uuid.uuid4().hex}{ext}"
    hw_dir = os.path.join(settings.upload_dir, "homework")
    os.makedirs(hw_dir, exist_ok=True)
    async with aiofiles.open(os.path.join(hw_dir, filename), "wb") as out:
        await out.write(content)

    file_type = "image" if (mime or file.content_type or "").startswith("image/") else "file"

    await log_audit(
        db,
        action="homework.file_upload",
        user=current_user,
        entity_type="homework_file",
        details={"filename": file.filename, "size": len(content), "type": file_type},
        request=request,
    )

    return {"url": f"/uploads/homework/{filename}", "type": file_type, "name": file.filename}


@router.post("/", response_model=HomeworkOut)
async def create_homework(request: Request, data: HomeworkCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    atts = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in (data.attachments or [])]
    file_url = data.file_url or (atts[0]["url"] if atts else None)
    file_type = data.file_type or (atts[0]["type"] if atts else None)

    hw = Homework(
        subject_id=data.subject_id,
        class_id=current_user.class_id,
        description=data.description,
        due_date=data.due_date,
        created_by=current_user.id,
        checked_by=[],
        file_url=file_url,
        file_type=file_type,
        attachments=atts,
    )
    db.add(hw)
    await db.commit()
    await db.refresh(hw)
    _normalize_hw_attachments(hw)

    await log_audit(
        db,
        action="homework.create",
        user=current_user,
        entity_type="homework",
        entity_id=hw.id,
        details={"subject_id": hw.subject_id, "due_date": hw.due_date, "desc": hw.description[:100]},
        request=request,
    )

    try:
        from backend.services.notification_scheduler import notify_new_homework
        await notify_new_homework(db, hw, current_user)
    except Exception as e:
        logger.warning("Error dispatching new homework notification: %s", e)

    return hw


@router.put("/{hw_id}", response_model=HomeworkOut)
async def update_homework(request: Request, hw_id: int, data: HomeworkUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Homework).where(Homework.id == hw_id))
    hw = result.scalar_one_or_none()
    if not hw or hw.class_id != current_user.class_id:
        raise HTTPException(404, "Hausaufgabe nicht gefunden")
    if hw.created_by != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)

    if data.subject_id is not None:
        hw.subject_id = data.subject_id
    if data.description is not None:
        hw.description = data.description
    if data.due_date is not None:
        hw.due_date = data.due_date
    if data.attachments is not None:
        atts = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in data.attachments]
        hw.attachments = atts
        hw.file_url = atts[0]["url"] if atts else None
        hw.file_type = atts[0]["type"] if atts else None

    await db.commit()
    await db.refresh(hw)
    _normalize_hw_attachments(hw)

    await log_audit(
        db,
        action="homework.update",
        user=current_user,
        entity_type="homework",
        entity_id=hw.id,
        details={"subject_id": hw.subject_id, "due_date": hw.due_date},
        request=request,
    )

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
async def delete_homework(request: Request, hw_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Homework).where(Homework.id == hw_id))
    hw = result.scalar_one_or_none()
    if not hw or hw.class_id != current_user.class_id:
        raise HTTPException(404)
    if hw.created_by != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)
    await db.delete(hw)
    await db.commit()

    await log_audit(
        db,
        action="homework.delete",
        user=current_user,
        entity_type="homework",
        entity_id=hw_id,
        details={"subject_id": hw.subject_id, "due_date": hw.due_date},
        request=request,
    )

    return {"ok": True}


# --- Lösungen (Homework Solutions) ---

@router.get("/{hw_id}/solutions", response_model=List[HomeworkSolutionOut])
async def list_solutions(hw_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify homework belongs to current user's class
    hw_res = await db.execute(select(Homework).where(Homework.id == hw_id))
    hw = hw_res.scalar_one_or_none()
    if not hw or hw.class_id != current_user.class_id:
        raise HTTPException(404, "Hausaufgabe nicht gefunden")

    result = await db.execute(
        select(HomeworkSolution)
        .options(selectinload(HomeworkSolution.user))
        .where(HomeworkSolution.homework_id == hw_id)
        .order_by(HomeworkSolution.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/{hw_id}/solutions", response_model=HomeworkSolutionOut)
async def create_solution(request: Request, hw_id: int, data: HomeworkSolutionCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    hw_res = await db.execute(select(Homework).where(Homework.id == hw_id))
    hw = hw_res.scalar_one_or_none()
    if not hw or hw.class_id != current_user.class_id:
        raise HTTPException(404, "Hausaufgabe nicht gefunden")

    atts = [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in (data.attachments or [])]
    text_val = (data.text or "").strip()
    if not text_val and not atts:
        raise HTTPException(400, "Bitte gib einen Text ein oder lade mindestens eine Datei hoch")

    sol = HomeworkSolution(
        homework_id=hw_id,
        user_id=current_user.id,
        text=text_val if text_val else None,
        attachments=atts,
    )
    db.add(sol)
    await db.commit()
    await db.refresh(sol)

    await log_audit(
        db,
        action="homework_solution.create",
        user=current_user,
        entity_type="homework_solution",
        entity_id=sol.id,
        details={"homework_id": hw_id, "attachments_count": len(atts)},
        request=request,
    )

    sol_res = await db.execute(
        select(HomeworkSolution)
        .options(selectinload(HomeworkSolution.user))
        .where(HomeworkSolution.id == sol.id)
    )
    return sol_res.scalar_one()


@router.delete("/{hw_id}/solutions/{sol_id}")
async def delete_solution(request: Request, hw_id: int, sol_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(HomeworkSolution).where(HomeworkSolution.id == sol_id, HomeworkSolution.homework_id == hw_id)
    )
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(404, "Lösung nicht gefunden")
    if sol.user_id != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403, "Keine Berechtigung")

    await db.delete(sol)
    await db.commit()

    await log_audit(
        db,
        action="homework_solution.delete",
        user=current_user,
        entity_type="homework_solution",
        entity_id=sol_id,
        details={"homework_id": hw_id},
        request=request,
    )

    return {"ok": True}
