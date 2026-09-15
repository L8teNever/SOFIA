from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func
from backend.database import get_db
from backend.auth import get_current_user, require_admin, require_super_admin
from backend.models.calendar_event import CalendarEvent
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.schemas import CalendarEventOut, CalendarEventCreate, HolidayImportRequest
from backend.services.holidays import GERMAN_STATES, fetch_holidays
from backend.services.audit_service import log_audit
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

@router.get("/", response_model=List[CalendarEventOut])
async def list_events(month: Optional[str] = None, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = select(CalendarEvent).where(
        CalendarEvent.class_id == current_user.class_id,
        or_(
            CalendarEvent.event_type != "personal",
            CalendarEvent.created_by == current_user.id
        )
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
    event = CalendarEvent(
        title=data.title,
        description=data.description,
        date=data.date,
        end_date=data.end_date,
        time=data.time,
        event_type=data.event_type,
        class_id=current_user.class_id,
        subject_id=data.subject_id,
        created_by=current_user.id,
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
    if event.class_id != current_user.class_id:
        raise HTTPException(403)
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
    event.event_type = data.event_type
    event.subject_id = data.subject_id

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
    if event.class_id != current_user.class_id:
        raise HTTPException(403)
    if event.event_type == "personal" and event.created_by != current_user.id:
        raise HTTPException(403, "Persönliche Termine können nur vom Ersteller gelöscht werden")
    title = event.title
    dt = event.date

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

