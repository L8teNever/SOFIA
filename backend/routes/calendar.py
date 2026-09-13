from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_
from backend.database import get_db
from backend.auth import get_current_user, require_admin, require_super_admin
from backend.models.calendar_event import CalendarEvent
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.schemas import CalendarEventOut, CalendarEventCreate, HolidayImportRequest
from backend.services.holidays import GERMAN_STATES, fetch_holidays
from typing import List, Optional

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

@router.get("/", response_model=List[CalendarEventOut])
async def list_events(month: Optional[str] = None, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = select(CalendarEvent).where(CalendarEvent.class_id == current_user.class_id)
    if month:
        first_day = f"{month}-01"
        last_day = f"{month}-31"
        query = query.where(
            CalendarEvent.date <= last_day,
            or_(
                CalendarEvent.end_date >= first_day,
                and_(CalendarEvent.end_date.is_(None), CalendarEvent.date >= first_day)
            )
        )
    result = await db.execute(query.order_by(CalendarEvent.date))
    return result.scalars().all()

@router.post("/", response_model=CalendarEventOut)
async def create_event(data: CalendarEventCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    event = CalendarEvent(
        title=data.title,
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

# --- Einzelne Termine löschen ---

@router.delete("/{event_id}")
async def delete_event(event_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(CalendarEvent).where(CalendarEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404)
    if event.class_id != current_user.class_id:
        raise HTTPException(403)
    if event.created_by != current_user.id and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(403)
    await db.delete(event)
    await db.commit()
    return {"ok": True}
