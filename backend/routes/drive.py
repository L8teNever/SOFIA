from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.drive_file import DriveFile
from backend.models.drive_topic import DriveTopic
from backend.models.subject import Subject
from backend.models.user import User
from backend.schemas import DriveFileOut, DriveFileUpdate, DriveTextUpdate, DriveTopicOut, DriveTopicCreate, DriveTopicRename
from backend.config import settings
from backend.services.virus_scanner import scan_file
from backend.services.compression import compress_lossless
from backend.services.audit_service import log_audit
from backend.gemini_client import classify_drive_file, GeminiError
from datetime import datetime, timezone
from typing import List, Optional
import aiofiles, uuid, os, logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/drive", tags=["drive"])

TEXT_EDITABLE_EXTS = {".md", ".markdown", ".txt"}

def _ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()

async def _get_or_create_topic(db: AsyncSession, class_id: int, subject_id: int, name: str) -> DriveTopic:
    """Keeps drive_topics in sync with whatever topic name a file is tagged
    with, so a topic typed at upload/move time immediately exists as a real
    folder (visible even once empty) rather than only appearing implicitly
    while at least one file references it."""
    name = name.strip()
    result = await db.execute(select(DriveTopic).where(DriveTopic.subject_id == subject_id, DriveTopic.name == name))
    topic = result.scalar_one_or_none()
    if topic:
        return topic
    topic = DriveTopic(class_id=class_id, subject_id=subject_id, name=name)
    db.add(topic)
    await db.flush()
    return topic

async def _serialize(db: AsyncSession, files: List[DriveFile]) -> List[dict]:
    if not files:
        return []
    uploader_ids = {f.uploader_id for f in files}
    subject_ids = {f.subject_id for f in files if f.subject_id}
    uploaders = {}
    if uploader_ids:
        res = await db.execute(select(User).where(User.id.in_(uploader_ids)))
        uploaders = {u.id: u for u in res.scalars().all()}
    subjects = {}
    if subject_ids:
        res = await db.execute(select(Subject).where(Subject.id.in_(subject_ids)))
        subjects = {s.id: s for s in res.scalars().all()}

    out = []
    for f in files:
        u = uploaders.get(f.uploader_id)
        s = subjects.get(f.subject_id) if f.subject_id else None
        out.append({
            "id": f.id,
            "class_id": f.class_id,
            "subject_id": f.subject_id,
            "subject_name": s.name if s else None,
            "topic": f.topic,
            "uploader_id": f.uploader_id,
            "uploader_name": (u.display_name or u.email.split("@")[0]) if u else None,
            "original_name": f.original_name,
            "file_size": f.file_size,
            "mime_type": f.mime_type,
            "created_at": f.created_at,
        })
    return out

@router.get("/", response_model=List[DriveFileOut])
async def list_drive_files(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        return []
    result = await db.execute(
        select(DriveFile).where(DriveFile.class_id == current_user.class_id).order_by(DriveFile.created_at.desc())
    )
    return await _serialize(db, list(result.scalars().all()))

@router.post("/upload", response_model=DriveFileOut)
async def upload_drive_file(
    request: Request,
    file: UploadFile = File(...),
    subject_id: str = Form(""),
    topic: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.class_id:
        raise HTTPException(400, "Keine Klasse zugewiesen")

    content = await file.read()
    if len(content) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")

    await scan_file(content, file.filename or "unknown", file.content_type)
    content, ext, mime = compress_lossless(content, file.filename or "", file.content_type)
    if not ext:
        ext = _ext(file.filename or "")

    resolved_subject_id = int(subject_id) if subject_id.strip().isdigit() else None
    resolved_topic = topic.strip() or None
    auto_sorted = False

    if resolved_subject_id is None:
        try:
            result = await db.execute(select(Subject).where(
                or_(
                    Subject.class_id == current_user.class_id,
                    Subject.is_global == True,  # noqa: E712
                    Subject.class_id.is_(None),
                )
            ))
            class_subjects = list(result.scalars().all())
        except Exception:
            class_subjects = []

        if class_subjects:
            existing = await db.execute(select(DriveFile).where(DriveFile.class_id == current_user.class_id))
            existing_files = list(existing.scalars().all())
            topics_by_subject: dict[str, list[str]] = {}
            subj_by_id = {s.id: s.name for s in class_subjects}
            for f in existing_files:
                if f.subject_id and f.topic:
                    name = subj_by_id.get(f.subject_id)
                    if name:
                        topics_by_subject.setdefault(name, [])
                        if f.topic not in topics_by_subject[name]:
                            topics_by_subject[name].append(f.topic)

            text_excerpt = None
            if ext in TEXT_EDITABLE_EXTS:
                try:
                    text_excerpt = content.decode("utf-8", errors="ignore")[:1500]
                except Exception:
                    text_excerpt = None

            try:
                suggestion = await classify_drive_file(
                    file.filename or "Datei", mime or file.content_type,
                    [s.name for s in class_subjects], topics_by_subject, text_excerpt,
                )
                if suggestion.get("subject"):
                    name_to_id = {s.name: s.id for s in class_subjects}
                    resolved_subject_id = name_to_id.get(suggestion["subject"])
                    if resolved_subject_id and not resolved_topic:
                        resolved_topic = suggestion.get("topic")
                    auto_sorted = resolved_subject_id is not None
            except Exception as e:
                logger.warning("Drive auto-sort failed, leaving file unsorted: %s", e)

    if resolved_subject_id and resolved_topic:
        await _get_or_create_topic(db, current_user.class_id, resolved_subject_id, resolved_topic)

    filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(settings.upload_dir, filename)
    os.makedirs(settings.upload_dir, exist_ok=True)
    async with aiofiles.open(dest, "wb") as out:
        await out.write(content)

    df = DriveFile(
        class_id=current_user.class_id,
        subject_id=resolved_subject_id,
        topic=resolved_topic,
        uploader_id=current_user.id,
        filename=filename,
        original_name=file.filename or filename,
        file_size=len(content),
        mime_type=mime or file.content_type,
    )
    db.add(df)
    await db.commit()
    await db.refresh(df)

    await log_audit(
        db, action="drive.upload", user=current_user, entity_type="drive_file", entity_id=df.id,
        details={"filename": df.original_name, "size": len(content), "auto_sorted": auto_sorted},
        request=request,
    )

    out = (await _serialize(db, [df]))[0]
    out["auto_sorted"] = auto_sorted
    return out

@router.get("/download/{file_id}")
async def download_drive_file(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    path = os.path.join(settings.upload_dir, df.filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=df.original_name, media_type=df.mime_type or "application/octet-stream")

@router.get("/preview/{file_id}")
async def preview_drive_file(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    path = os.path.join(settings.upload_dir, df.filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type=df.mime_type or "application/octet-stream", content_disposition_type="inline")

@router.get("/{file_id}/text")
async def get_drive_file_text(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    if _ext(df.original_name) not in TEXT_EDITABLE_EXTS:
        raise HTTPException(400, "Diese Datei kann nicht als Text bearbeitet werden")
    path = os.path.join(settings.upload_dir, df.filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    async with aiofiles.open(path, "r", encoding="utf-8", errors="replace") as fh:
        content = await fh.read()
    return {"content": content}

@router.put("/{file_id}/text", response_model=DriveFileOut)
async def update_drive_file_text(file_id: int, data: DriveTextUpdate, request: Request, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    if _ext(df.original_name) not in TEXT_EDITABLE_EXTS:
        raise HTTPException(400, "Diese Datei kann nicht als Text bearbeitet werden")
    path = os.path.join(settings.upload_dir, df.filename)
    encoded = data.content.encode("utf-8")
    async with aiofiles.open(path, "wb") as fh:
        await fh.write(encoded)
    df.file_size = len(encoded)
    await db.commit()
    await db.refresh(df)

    await log_audit(
        db, action="drive.edit_text", user=current_user, entity_type="drive_file", entity_id=df.id,
        details={"filename": df.original_name}, request=request,
    )
    return (await _serialize(db, [df]))[0]

@router.put("/{file_id}", response_model=DriveFileOut)
async def update_drive_file(file_id: int, data: DriveFileUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    df.subject_id = data.subject_id
    df.topic = (data.topic or "").strip() or None
    if df.subject_id and df.topic:
        await _get_or_create_topic(db, current_user.class_id, df.subject_id, df.topic)
    await db.commit()
    await db.refresh(df)
    return (await _serialize(db, [df]))[0]

# --- Topic folders ---

@router.get("/topics", response_model=List[DriveTopicOut])
async def list_drive_topics(subject_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(DriveTopic).where(
        DriveTopic.class_id == current_user.class_id, DriveTopic.subject_id == subject_id
    ).order_by(DriveTopic.name))
    topics = list(result.scalars().all())
    counts_res = await db.execute(select(DriveFile).where(
        DriveFile.class_id == current_user.class_id, DriveFile.subject_id == subject_id
    ))
    counts: dict[str, int] = {}
    for f in counts_res.scalars().all():
        if f.topic:
            counts[f.topic] = counts.get(f.topic, 0) + 1
    return [
        {"id": t.id, "class_id": t.class_id, "subject_id": t.subject_id, "name": t.name, "file_count": counts.get(t.name, 0)}
        for t in topics
    ]

@router.post("/topics", response_model=DriveTopicOut)
async def create_drive_topic(data: DriveTopicCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Name darf nicht leer sein")
    topic = await _get_or_create_topic(db, current_user.class_id, data.subject_id, name)
    await db.commit()
    await db.refresh(topic)
    return {"id": topic.id, "class_id": topic.class_id, "subject_id": topic.subject_id, "name": topic.name, "file_count": 0}

@router.put("/topics/{topic_id}", response_model=DriveTopicOut)
async def rename_drive_topic(topic_id: int, data: DriveTopicRename, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(DriveTopic).where(DriveTopic.id == topic_id))
    topic = result.scalar_one_or_none()
    if not topic or topic.class_id != current_user.class_id:
        raise HTTPException(404)
    new_name = data.name.strip()
    if not new_name:
        raise HTTPException(400, "Name darf nicht leer sein")
    old_name = topic.name
    topic.name = new_name

    files_result = await db.execute(select(DriveFile).where(
        DriveFile.class_id == current_user.class_id, DriveFile.subject_id == topic.subject_id, DriveFile.topic == old_name
    ))
    file_count = 0
    for f in files_result.scalars().all():
        f.topic = new_name
        file_count += 1

    await db.commit()
    await db.refresh(topic)
    return {"id": topic.id, "class_id": topic.class_id, "subject_id": topic.subject_id, "name": topic.name, "file_count": file_count}

@router.delete("/topics/{topic_id}")
async def delete_drive_topic(topic_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(DriveTopic).where(DriveTopic.id == topic_id))
    topic = result.scalar_one_or_none()
    if not topic or topic.class_id != current_user.class_id:
        raise HTTPException(404)

    # Deleting a folder un-files its contents (moved to "Ohne Thema") rather
    # than deleting the files themselves — matches how a folder delete
    # should behave, and avoids surprise data loss.
    files_result = await db.execute(select(DriveFile).where(
        DriveFile.class_id == current_user.class_id, DriveFile.subject_id == topic.subject_id, DriveFile.topic == topic.name
    ))
    for f in files_result.scalars().all():
        f.topic = None

    await db.delete(topic)
    await db.commit()
    return {"ok": True}

@router.delete("/{file_id}")
async def delete_drive_file(request: Request, file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    if df.uploader_id != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)
    path = os.path.join(settings.upload_dir, df.filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    original_name = df.original_name
    await db.delete(df)
    await db.commit()

    await log_audit(
        db, action="drive.delete", user=current_user, entity_type="drive_file", entity_id=file_id,
        details={"filename": original_name}, request=request,
    )
    return {"ok": True}

async def _get_visible(db: AsyncSession, file_id: int, current_user: User) -> DriveFile:
    result = await db.execute(select(DriveFile).where(DriveFile.id == file_id))
    df = result.scalar_one_or_none()
    if not df or df.class_id != current_user.class_id:
        raise HTTPException(404)
    return df
