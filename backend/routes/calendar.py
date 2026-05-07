from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_admin
from backend.models.calendar_event import CalendarEvent
from backend.models.user import User
from backend.schemas import CalendarEventOut, CalendarEventCreate
from typing import List, Optional

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

@router.get("/", response_model=List[CalendarEventOut])
async def list_events(month: Optional[str] = None, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = select(CalendarEvent).where(CalendarEvent.class_id == current_user.class_id)
    if month:
        query = query.where(CalendarEvent.date.startswith(month))
    result = await db.execute(query.order_by(CalendarEvent.date))
    return result.scalars().all()

@router.post("/", response_model=CalendarEventOut)
async def create_event(data: CalendarEventCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    event = CalendarEvent(
        title=data.title,
        date=data.date,
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
