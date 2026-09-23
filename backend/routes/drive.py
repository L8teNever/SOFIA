from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
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
from datetime import datetime, timezone
from typing import List, Optional
import aiofiles, uuid, os, logging, html, re

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/drive", tags=["drive"])

TEXT_EDITABLE_EXTS = {".md", ".markdown", ".txt"}
# Leftover per-lesson date folders from the old upload sheet. Empty ones
# are still cleaned up on delete/move so they don't linger.
DATE_TOPIC_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

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

async def _maybe_delete_empty_date_topic(db: AsyncSession, class_id: int, subject_id: Optional[int], topic_name: Optional[str]):
    """A per-lesson date folder (see DATE_TOPIC_RE above) is incidental —
    nobody deliberately decided "16.09.2026" should exist as a folder, it's
    just where that day's files happened to land via the upload sheet's
    subject+day picker. Once the last file in one is gone (deleted, or
    moved elsewhere), the empty date folder left behind has no purpose, so
    it's removed automatically instead of lingering. A manually-named topic
    (anything not matching the date pattern) is left alone even when
    empty — a person deliberately created that one and might want to keep
    it, e.g. to prepare it before any files exist yet."""
    if not subject_id or not topic_name or not DATE_TOPIC_RE.match(topic_name):
        return
    remaining = await db.execute(select(DriveFile).where(
        DriveFile.class_id == class_id, DriveFile.subject_id == subject_id, DriveFile.topic == topic_name,
    ))
    if remaining.scalar_one_or_none():
        return
    topic_result = await db.execute(select(DriveTopic).where(
        DriveTopic.class_id == class_id, DriveTopic.subject_id == subject_id, DriveTopic.name == topic_name,
    ))
    topic = topic_result.scalar_one_or_none()
    if topic:
        await db.delete(topic)

async def _dedupe_original_name(db: AsyncSession, class_id: int, subject_id: Optional[int], topic: Optional[str], name: str, exclude_id: Optional[int] = None) -> str:
    """Two files with the exact same visible name in the exact same folder
    used to be harmless (they were only ever addressed by numeric id) — now
    that a file's URL is built from subject/topic/filename instead (see
    public_router below), the name has to actually be unique within its
    folder for that URL to resolve to the right file. Appends " (2)",
    " (3)", ... until it finds a name nothing else in the folder is using."""
    base, ext = os.path.splitext(name)
    candidate = name
    n = 2
    while True:
        query = select(DriveFile).where(
            DriveFile.class_id == class_id, DriveFile.subject_id == subject_id,
            DriveFile.topic == topic, DriveFile.original_name == candidate,
        )
        if exclude_id is not None:
            query = query.where(DriveFile.id != exclude_id)
        result = await db.execute(query)
        if not result.scalar_one_or_none():
            return candidate
        candidate = f"{base} ({n}){ext}"
        n += 1

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
            "is_lecture_notes": f.is_lecture_notes,
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

    if resolved_subject_id and resolved_topic:
        await _get_or_create_topic(db, current_user.class_id, resolved_subject_id, resolved_topic)

    original_name = await _dedupe_original_name(
        db, current_user.class_id, resolved_subject_id, resolved_topic,
        file.filename or f"Datei{ext}",
    )

    filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(settings.drive_storage_dir, filename)
    os.makedirs(settings.drive_storage_dir, exist_ok=True)
    async with aiofiles.open(dest, "wb") as out:
        await out.write(content)

    df = DriveFile(
        class_id=current_user.class_id,
        subject_id=resolved_subject_id,
        topic=resolved_topic,
        is_lecture_notes=False,
        uploader_id=current_user.id,
        filename=filename,
        original_name=original_name,
        file_size=len(content),
        mime_type=mime or file.content_type,
    )
    db.add(df)
    await db.commit()
    await db.refresh(df)

    await log_audit(
        db, action="drive.upload", user=current_user, entity_type="drive_file", entity_id=df.id,
        details={"filename": df.original_name, "size": len(content)},
        request=request,
    )

    return (await _serialize(db, [df]))[0]

@router.get("/download/{file_id}")
async def download_drive_file(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    path = os.path.join(settings.drive_storage_dir, df.filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=df.original_name, media_type=df.mime_type or "application/octet-stream")

@router.get("/preview/{file_id}")
async def preview_drive_file(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    path = os.path.join(settings.drive_storage_dir, df.filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type=df.mime_type or "application/octet-stream", content_disposition_type="inline")

VIEW_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}

@router.get("/view/{file_id}", response_class=HTMLResponse)
async def view_drive_file(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A tiny standalone page around /preview/{id} — opened in a new tab for
    the file instead of linking straight to the raw file, so there's still a
    way back into the app (linking straight to the raw file leaves the
    browser's native viewer with no app chrome at all, so closing the tab
    was the only way back). Uses a normal viewport (no user-scalable=no)
    so the browser's own native pinch-zoom works here even for images."""
    df = await _get_visible(db, file_id, current_user)
    ext = _ext(df.original_name)
    name_esc = html.escape(df.original_name)

    if ext in VIEW_IMAGE_EXTS:
        body = f'<img src="/api/v1/drive/preview/{file_id}" alt="{name_esc}">'
    elif ext == ".pdf":
        body = f'<iframe src="/api/v1/drive/preview/{file_id}"></iframe>'
    else:
        body = (
            f'<div class="dv-empty">Für diesen Dateityp gibt es keine Vorschau im Browser.'
            f'<br><a href="/api/v1/drive/download/{file_id}">Datei herunterladen</a></div>'
        )

    return HTMLResponse(f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name_esc}</title>
<style>
  html, body {{ margin:0; height:100%; background:#f3edf7; font-family:system-ui,-apple-system,sans-serif; }}
  .dv-header {{ display:flex; align-items:center; gap:10px; padding:10px 14px; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,0.08); position:sticky; top:0; }}
  .dv-btn {{ display:flex; align-items:center; justify-content:center; width:36px; height:36px; border-radius:50%; background:rgba(103,80,164,0.1); color:#442b7a; text-decoration:none; flex-shrink:0; font-size:1.1rem; }}
  .dv-title {{ font-weight:700; font-size:0.92rem; color:#1c1b1f; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; min-width:0; }}
  .dv-body {{ height:calc(100vh - 57px); display:flex; align-items:center; justify-content:center; overflow:auto; box-sizing:border-box; }}
  .dv-body img {{ max-width:100%; display:block; }}
  .dv-body iframe {{ width:100%; height:100%; border:none; }}
  .dv-empty {{ text-align:center; padding:40px 24px; opacity:0.6; line-height:1.6; }}
</style>
</head>
<body>
  <div class="dv-header">
    <a class="dv-btn" href="/drive" title="Zurück zu Drive">&#8592;</a>
    <div class="dv-title">{name_esc}</div>
    <a class="dv-btn" href="/api/v1/drive/download/{file_id}" download title="Herunterladen">&#8595;</a>
  </div>
  <div class="dv-body">{body}</div>
</body>
</html>""")

@router.get("/{file_id}/text")
async def get_drive_file_text(file_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _get_visible(db, file_id, current_user)
    if _ext(df.original_name) not in TEXT_EDITABLE_EXTS:
        raise HTTPException(400, "Diese Datei kann nicht als Text bearbeitet werden")
    path = os.path.join(settings.drive_storage_dir, df.filename)
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
    path = os.path.join(settings.drive_storage_dir, df.filename)
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
    old_subject_id, old_topic = df.subject_id, df.topic
    df.subject_id = data.subject_id
    df.topic = (data.topic or "").strip() or None
    if df.subject_id and df.topic:
        await _get_or_create_topic(db, current_user.class_id, df.subject_id, df.topic)
    # Moving into a folder that already has a same-named file would
    # otherwise leave two files answering to the same subject/topic/
    # filename URL (see public_router below) — same reasoning as the
    # upload-time dedupe.
    if (old_subject_id, old_topic) != (df.subject_id, df.topic):
        df.original_name = await _dedupe_original_name(db, current_user.class_id, df.subject_id, df.topic, df.original_name, exclude_id=df.id)
    await db.commit()
    await db.refresh(df)

    if (old_subject_id, old_topic) != (df.subject_id, df.topic):
        await _maybe_delete_empty_date_topic(db, current_user.class_id, old_subject_id, old_topic)
        await db.commit()

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
    # Unlike a single file (which only ever belongs to one uploader), a
    # folder can hold files from the whole class at once — deleting it
    # un-files all of them in one action, so this needs the same elevated
    # role individual file deletion already requires, not just "any
    # classmate can do it".
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)

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
    path = os.path.join(settings.drive_storage_dir, df.filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    original_name = df.original_name
    class_id, subject_id, topic = df.class_id, df.subject_id, df.topic
    await db.delete(df)
    await db.commit()

    await _maybe_delete_empty_date_topic(db, class_id, subject_id, topic)
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

# --- Human-readable file URLs ---
# A plain /api/v1/drive/preview/{id} tells a human nothing about what the
# link actually is. These serve the same download/preview/view content at
# a real-looking path instead — /drive/{subject}/{topic}/{filename} — with
# no id anywhere in it. The file is looked up BY that subject/topic/filename
# combination, scoped to the requesting user's own class in the same query
# that does the lookup (see _find_by_path) — same effective authorization
# as _get_visible()'s class check everywhere else in this file, just
# expressed as a WHERE clause instead of a separate check, since there's no
# id here to look up first. Two files that would otherwise collide on the
# same folder+filename get disambiguated at upload/move time instead (see
# _dedupe_original_name) so this lookup is never actually ambiguous.
public_router = APIRouter(prefix="/drive", tags=["drive-files"])

async def _find_by_path(db: AsyncSession, current_user: User, subject: str, topic: str, filename: str) -> DriveFile:
    subject_id = None
    if subject != "Unsortiert":
        subj_result = await db.execute(select(Subject).where(
            Subject.name == subject,
            or_(Subject.class_id == current_user.class_id, Subject.is_global == True, Subject.class_id.is_(None)),  # noqa: E712
        ))
        subj = subj_result.scalars().first()
        if not subj:
            raise HTTPException(404)
        subject_id = subj.id

    topic_val = None if topic == "Ohne-Thema" else topic
    result = await db.execute(select(DriveFile).where(
        DriveFile.class_id == current_user.class_id,
        DriveFile.subject_id == subject_id,
        DriveFile.topic == topic_val,
        DriveFile.original_name == filename,
    ))
    df = result.scalar_one_or_none()
    if not df:
        raise HTTPException(404)
    return df

@public_router.get("/{subject}/{topic}/{filename}/download")
async def pretty_download_drive_file(subject: str, topic: str, filename: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _find_by_path(db, current_user, subject, topic, filename)
    return await download_drive_file(df.id, db, current_user)

@public_router.get("/{subject}/{topic}/{filename}/raw")
async def pretty_preview_drive_file(subject: str, topic: str, filename: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _find_by_path(db, current_user, subject, topic, filename)
    return await preview_drive_file(df.id, db, current_user)

@public_router.get("/{subject}/{topic}/{filename}")
async def pretty_view_drive_file(subject: str, topic: str, filename: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    df = await _find_by_path(db, current_user, subject, topic, filename)
    return await preview_drive_file(df.id, db, current_user)
