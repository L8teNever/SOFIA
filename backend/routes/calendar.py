from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func
from backend.database import get_db
from backend.auth import get_current_user, require_admin, require_super_admin
from backend.config import settings
from backend.models.calendar_event import CalendarEvent
from backend.models.calendar_category import CalendarCategory
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.schemas import CalendarEventOut, CalendarEventCreate, HolidayImportRequest
from backend.services.holidays import GERMAN_STATES, fetch_holidays
from backend.services.audit_service import log_audit
from backend.services.virus_scanner import scan_file
from backend.services.compression import compress_lossless
from typing import List, Optional
from pydantic import BaseModel
import re
import logging
import os
import uuid
import aiofiles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

_CAL_URL_PREFIX = "/uploads/calendar/"


def _attachment_dicts(items) -> list:
    out = []
    for a in items or []:
        data = a.model_dump() if hasattr(a, "model_dump") else dict(a)
        url = data.get("url") or ""
        name = os.path.basename(url)
        if not url.startswith(_CAL_URL_PREFIX) or not name or name != url[len(_CAL_URL_PREFIX):]:
            raise HTTPException(400, "Ungültiger Dateianhang")
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(400, "Ungültiger Dateianhang")
        kind = "image" if data.get("type") == "image" else "file"
        out.append({"url": url, "type": kind, "name": (data.get("name") or name)[:180]})
    return out


def _calendar_file_path(url: str):
    if not url or not url.startswith(_CAL_URL_PREFIX):
        return None
    name = url[len(_CAL_URL_PREFIX):]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    return os.path.join(settings.upload_dir, "calendar", name)


def _remove_dropped_files(previous, kept):
    keep_urls = {a.get("url") for a in kept}
    for old in previous or []:
        if not isinstance(old, dict):
            continue
        url = old.get("url")
        if not url or url in keep_urls:
            continue
        path = _calendar_file_path(url)
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning("Could not remove calendar file %s", path)


@router.post("/upload")
async def upload_event_file(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content = await file.read()
    if len(content) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")

    await scan_file(content, file.filename or "unknown", file.content_type)
    content, ext, mime = compress_lossless(content, file.filename or "", file.content_type)
    if not ext:
        ext = os.path.splitext(file.filename or "")[1]

    filename = f"{uuid.uuid4().hex}{ext}"
    dest_dir = os.path.join(settings.upload_dir, "calendar")
    os.makedirs(dest_dir, exist_ok=True)
    async with aiofiles.open(os.path.join(dest_dir, filename), "wb") as out:
        await out.write(content)

    file_type = "image" if (mime or file.content_type or "").startswith("image/") else "file"
    await log_audit(
        db,
        action="calendar.file_upload",
        user=current_user,
        entity_type="calendar_file",
        details={"filename": file.filename, "size": len(content), "type": file_type},
        request=request,
    )
    return {"url": f"{_CAL_URL_PREFIX}{filename}", "type": file_type, "name": file.filename}

# ---- Eigene Kategorien (vom Super-Admin angelegt, nur fuer deren Mitglieder sichtbar) ----

async def _my_category_ids(db: AsyncSession, user: User) -> set:
    cats = (await db.execute(select(CalendarCategory))).scalars().all()
    return {c.id for c in cats if user.id in (c.member_ids or [])}


def _etype(ev) -> str:
    return getattr(ev.event_type, "value", str(ev.event_type))


async def _check_event_access(db: AsyncSession, event: CalendarEvent, user: User):
    """Termin einer eigenen Kategorie: nur deren Mitglieder. Sonst: nur die eigene Klasse."""
    if _etype(event) == "custom":
        if event.category_id not in await _my_category_ids(db, user):
            raise HTTPException(403)
    elif event.class_id != user.class_id:
        raise HTTPException(403)


async def _resolve_type(db: AsyncSession, data, user: User):
    """(event_type, category_id) - eine eigene Kategorie duerfen nur ihre Mitglieder benutzen."""
    if data.event_type == "custom":
        if not data.category_id or data.category_id not in await _my_category_ids(db, user):
            raise HTTPException(403, "Diese Kategorie darfst du nicht benutzen")
        return "custom", data.category_id
    return data.event_type, None


class CategoryIn(BaseModel):
    name: str
    color: str = "#6750a4"
    member_ids: List[int] = []


def _category_out(c: CalendarCategory, full: bool) -> dict:
    out = {"id": c.id, "name": c.name, "color": c.color}
    if full:
        out["member_ids"] = list(c.member_ids or [])
    return out


async def _clean_category(db: AsyncSession, data: CategoryIn) -> dict:
    name = (data.name or "").strip()[:60]
    if not name:
        raise HTTPException(400, "Name fehlt")
    color = data.color if re.fullmatch(r"#[0-9a-fA-F]{6}", data.color or "") else "#6750a4"
    wanted = {int(i) for i in data.member_ids or []}
    existing = set((await db.execute(select(User.id).where(User.id.in_(wanted)))).scalars().all()) if wanted else set()
    return {"name": name, "color": color, "member_ids": sorted(existing)}


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Jeder sieht die Kategorien, in denen er Mitglied ist; der Super-Admin alle (mit Mitgliedern)."""
    cats = (await db.execute(select(CalendarCategory).order_by(CalendarCategory.name))).scalars().all()
    is_super = current_user.role == "super_admin"
    return [{**_category_out(c, is_super), "member": current_user.id in (c.member_ids or [])}
            for c in cats if is_super or current_user.id in (c.member_ids or [])]


@router.post("/categories")
async def create_category(request: Request, data: CategoryIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    c = CalendarCategory(**(await _clean_category(db, data)), created_by=current_user.id)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    await log_audit(db, action="calendar.category_create", user=current_user, entity_type="calendar_category",
                    entity_id=c.id, details={"name": c.name, "members": len(c.member_ids or [])}, request=request)
    return _category_out(c, True)


@router.put("/categories/{cat_id}")
async def update_category(request: Request, cat_id: int, data: CategoryIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    c = (await db.execute(select(CalendarCategory).where(CalendarCategory.id == cat_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    for k, v in (await _clean_category(db, data)).items():
        setattr(c, k, v)
    await db.commit()
    await log_audit(db, action="calendar.category_update", user=current_user, entity_type="calendar_category",
                    entity_id=c.id, details={"name": c.name, "members": len(c.member_ids or [])}, request=request)
    return _category_out(c, True)


@router.delete("/categories/{cat_id}")
async def delete_category(request: Request, cat_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    """Loescht die Kategorie samt ihrer Termine."""
    c = (await db.execute(select(CalendarCategory).where(CalendarCategory.id == cat_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    evs = (await db.execute(select(CalendarEvent).where(CalendarEvent.category_id == cat_id))).scalars().all()
    for ev in evs:
        _remove_dropped_files(list(ev.attachments or []), [])
        await db.delete(ev)
    name = c.name
    await db.delete(c)
    await db.commit()
    await log_audit(db, action="calendar.category_delete", user=current_user, entity_type="calendar_category",
                    entity_id=cat_id, details={"name": name, "events": len(evs)}, request=request)
    return {"ok": True, "deleted_events": len(evs)}


@router.get("/", response_model=List[CalendarEventOut])
async def list_events(month: Optional[str] = None, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    my_cats = await _my_category_ids(db, current_user)
    class_events = and_(
        CalendarEvent.class_id == current_user.class_id,
        CalendarEvent.event_type != "custom",
        or_(
            CalendarEvent.event_type != "personal",
            CalendarEvent.created_by == current_user.id
        ),
    )
    query = select(CalendarEvent).where(
        or_(class_events, and_(CalendarEvent.event_type == "custom", CalendarEvent.category_id.in_(my_cats)))
        if my_cats else class_events
    )
    if month:
        first_day = f"{month}-01"
        last_day = f"{month}-31"
        query = query.where(
            CalendarEvent.date <= last_day,
            func.coalesce(func.nullif(CalendarEvent.end_date, ""), CalendarEvent.date) >= first_day
        )
    result = await db.execute(query.order_by(CalendarEvent.date))
    return result.scalars().all()


@router.post("/", response_model=CalendarEventOut)
async def create_event(request: Request, data: CalendarEventCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    event_type, category_id = await _resolve_type(db, data, current_user)
    class_id = current_user.class_id
    if not class_id:
        first = (await db.execute(select(ClassGroup.id).order_by(ClassGroup.id))).scalars().first()
        if event_type != "custom" or not first:
            raise HTTPException(400, "Keine Klasse")
        class_id = first  # Termin einer eigenen Kategorie gehoert keiner Klasse; die Spalte braucht trotzdem eine
    event = CalendarEvent(
        title=data.title,
        description=data.description,
        date=data.date,
        end_date=data.end_date,
        time=data.time,
        event_type=event_type,
        category_id=category_id,
        class_id=class_id,
        subject_id=data.subject_id if event_type != "custom" else None,
        created_by=current_user.id,
        attachments=_attachment_dicts(data.attachments),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    await log_audit(
        db,
        action="calendar.event_create",
        user=current_user,
        entity_type="calendar_event",
        entity_id=event.id,
        details={"title": event.title, "date": event.date, "type": event.event_type},
        request=request,
    )

    try:
        from backend.services.notification_scheduler import notify_new_event
        await notify_new_event(db, event, current_user)
    except Exception as e:
        logger.warning("Error dispatching new event notification: %s", e)

    try:
        from backend.services.google_sync_service import sync_event_created
        if event_type != "custom":  # synct an alle Google-Konten der Klasse - nichts fuer Kategorie-Termine
            await sync_event_created(db, event)
    except Exception as e:
        logger.warning("Error syncing new event to Google Calendar: %s", e)

    return event


# --- Schulferien Endpunkte ---

@router.get("/holidays/states")
async def get_holiday_states(_: User = Depends(get_current_user)):
    return GERMAN_STATES

@router.get("/holidays/preview")
async def preview_holidays(state: str, year: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    holidays = fetch_holidays(state, year)
    class_id = current_user.class_id
    existing = (await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.class_id == class_id,
            CalendarEvent.event_type == "holiday"
        )
    )).scalars().all()
    existing_set = {(e.title, e.date) for e in existing}

    for h in holidays:
        h["imported"] = (h["name"], h["start"]) in existing_set
    return holidays

@router.post("/holidays/import")
async def import_holidays(data: HolidayImportRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    holidays = fetch_holidays(data.state, data.year)
    if not holidays:
        raise HTTPException(400, "Keine Ferientermine gefunden")

    target_class_ids = []
    if data.class_id:
        target_class_ids = [data.class_id]
    elif current_user.role == "super_admin":
        all_classes = (await db.execute(select(ClassGroup))).scalars().all()
        target_class_ids = [c.id for c in all_classes]
        if not target_class_ids and current_user.class_id:
            target_class_ids = [current_user.class_id]
    else:
        target_class_ids = [current_user.class_id] if current_user.class_id else []

    if not target_class_ids:
        raise HTTPException(400, "Keine Zielklasse vorhanden")

    created_count = 0
    for cid in target_class_ids:
        existing_events = (await db.execute(
            select(CalendarEvent).where(
                CalendarEvent.class_id == cid,
                CalendarEvent.event_type == "holiday"
            )
        )).scalars().all()
        existing_keys = {(e.title, e.date) for e in existing_events}

        for h in holidays:
            if (h["name"], h["start"]) not in existing_keys:
                ev = CalendarEvent(
                    title=h["name"],
                    date=h["start"],
                    end_date=h["end"],
                    time=None,
                    event_type="holiday",
                    class_id=cid,
                    subject_id=None,
                    created_by=current_user.id
                )
                db.add(ev)
                created_count += 1
                existing_keys.add((h["name"], h["start"]))

    await db.commit()
    return {"ok": True, "imported_count": created_count, "holidays_found": len(holidays)}

@router.delete("/holidays")
async def delete_all_holidays(
    class_id: Optional[int] = None,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    query = select(CalendarEvent).where(CalendarEvent.event_type == "holiday")
    if class_id:
        query = query.where(CalendarEvent.class_id == class_id)
    elif current_user.role != "super_admin":
        query = query.where(CalendarEvent.class_id == current_user.class_id)

    if year:
        query = query.where(CalendarEvent.date.startswith(str(year)))

    events_to_del = (await db.execute(query)).scalars().all()
    count = len(events_to_del)
    for ev in events_to_del:
        await db.delete(ev)
    await db.commit()
    return {"ok": True, "deleted_count": count}

# --- Einzelne Termine bearbeiten & löschen ---

@router.put("/{event_id}", response_model=CalendarEventOut)
async def update_event(request: Request, event_id: int, data: CalendarEventCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(CalendarEvent).where(CalendarEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404, "Termin nicht gefunden")
    await _check_event_access(db, event, current_user)
    if event.event_type == "personal" and event.created_by != current_user.id:
        raise HTTPException(403, "Persönliche Termine können nur vom Ersteller bearbeitet werden")
    # Any other event is class-wide (everyone in the class can already see
    # it), so any class member can edit it too — not just the creator or
    # an admin. Only "personal" events (private to their creator) keep the
    # creator-only restriction above.

    event.title = data.title
    event.description = data.description
    event.date = data.date
    event.end_date = data.end_date
    event.time = data.time
    new_type, new_cat = await _resolve_type(db, data, current_user)
    if _etype(event) == "custom" and new_type != "custom":
        if not current_user.class_id:
            raise HTTPException(400, "Keine Klasse")
        event.class_id = current_user.class_id  # wird ein normaler Klassentermin
    event.event_type = new_type
    event.category_id = new_cat
    event.subject_id = data.subject_id if new_type != "custom" else None
    if data.attachments is not None:
        previous = list(event.attachments or [])
        event.attachments = _attachment_dicts(data.attachments)
        _remove_dropped_files(previous, event.attachments)

    await db.commit()
    await db.refresh(event)

    await log_audit(
        db,
        action="calendar.event_update",
        user=current_user,
        entity_type="calendar_event",
        entity_id=event.id,
        details={"title": event.title, "date": event.date, "type": event.event_type},
        request=request,
    )

    try:
        from backend.services.google_sync_service import sync_event_updated
        await sync_event_updated(db, event)
    except Exception as e:
        logger.warning("Error syncing updated event to Google Calendar: %s", e)

    return event

@router.delete("/{event_id}")
async def delete_event(request: Request, event_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(CalendarEvent).where(CalendarEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404)
    await _check_event_access(db, event, current_user)
    if event.event_type == "personal" and event.created_by != current_user.id:
        raise HTTPException(403, "Persönliche Termine können nur vom Ersteller gelöscht werden")
    title = event.title
    dt = event.date
    _remove_dropped_files(list(event.attachments or []), [])

    try:
        from backend.services.google_sync_service import sync_event_deleted
        await sync_event_deleted(db, event.id)
    except Exception as e:
        logger.warning("Error removing deleted event from Google Calendar: %s", e)

    await db.delete(event)
    await db.commit()

    await log_audit(
        db,
        action="calendar.event_delete",
        user=current_user,
        entity_type="calendar_event",
        entity_id=event_id,
        details={"title": title, "date": dt},
        request=request,
    )

    return {"ok": True}

