from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.shared_file import SharedFile, ShareVisibility
from backend.models.user import User
from backend.schemas import SharedFileOut
from backend.config import settings
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import aiofiles, uuid, os, json

router = APIRouter(prefix="/api/v1/files", tags=["files"])

@router.get("/", response_model=List[SharedFileOut])
async def list_files(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    result = await db.execute(select(SharedFile).where(SharedFile.expires_at > now))
    all_files = result.scalars().all()
    visible = []
    for f in all_files:
        if f.visibility == ShareVisibility.all:
            visible.append(f)
        elif f.visibility == ShareVisibility.class_ and f.class_id == current_user.class_id:
            visible.append(f)
        elif f.visibility in (ShareVisibility.person, ShareVisibility.people):
            if current_user.id in (f.visible_to or []) or f.uploader_id == current_user.id:
                visible.append(f)
    return visible

@router.post("/", response_model=SharedFileOut)
async def upload_file(
    file: UploadFile = File(...),
    visibility: str = Form("class"),
    visible_to: str = Form("[]"),
    expires_minutes: int = Form(45),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if file.size and file.size > settings.max_file_size:
        raise HTTPException(413, "File too large")
    ext = os.path.splitext(file.filename or "")[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(settings.upload_dir, filename)
    os.makedirs(settings.upload_dir, exist_ok=True)
    async with aiofiles.open(dest, "wb") as out:
        content = await file.read()
        await out.write(content)
    expires = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    sf = SharedFile(
        uploader_id=current_user.id,
        filename=filename,
        original_name=file.filename or filename,
        file_size=len(content),
        mime_type=file.content_type,
        visibility=ShareVisibility(visibility),
        visible_to=json.loads(visible_to),
        class_id=current_user.class_id,
        expires_at=expires,
    )
    db.add(sf)
    await db.commit()
    await db.refresh(sf)
    return sf

@router.get("/download/{file_id}")
async def download_file(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SharedFile).where(SharedFile.id == file_id))
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(404)
    now = datetime.now(timezone.utc)
    if sf.expires_at.replace(tzinfo=timezone.utc) < now:
        raise HTTPException(410, "File expired")
    path = os.path.join(settings.upload_dir, sf.filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=sf.original_name, media_type=sf.mime_type or "application/octet-stream")

@router.delete("/{file_id}")
async def delete_file(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SharedFile).where(SharedFile.id == file_id))
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(404)
    if sf.uploader_id != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)
    path = os.path.join(settings.upload_dir, sf.filename)
    if os.path.exists(path):
        os.remove(path)
    await db.delete(sf)
    await db.commit()
    return {"ok": True}
