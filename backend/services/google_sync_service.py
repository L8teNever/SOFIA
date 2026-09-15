"""Pushes SOFIA calendar events / homework into each connected user's own
Google Calendar / Google Tasks. Every entry point here is meant to be called
right after a successful commit and wrapped in try/except by the caller
(matching the existing notify_new_event/notify_new_homework pattern) — a
sync failure must never break the underlying create/update/delete.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone

from backend.models.google_account import GoogleAccount
from backend.models.google_sync_map import GoogleSyncedEvent, GoogleSyncedTask
from backend.models.calendar_event import CalendarEvent
from backend.models.homework import Homework
from backend.models.user import User
from backend.services.crypto import decrypt_value, encrypt_value
from backend import google_client
import logging

logger = logging.getLogger(__name__)

async def _valid_access_token(db: AsyncSession, ga: GoogleAccount) -> str:
    """Returns a usable access token, refreshing it first if it's expired
    (or close to it) and persisting the refreshed token back to the DB."""
    expiry = ga.token_expiry
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry and expiry > datetime.now(timezone.utc):
        return decrypt_value(ga.access_token_enc)

    refresh_token = decrypt_value(ga.refresh_token_enc)
    tokens = await google_client.refresh_access_token(refresh_token)
    ga.access_token_enc = encrypt_value(tokens["access_token"])
    expires_in = tokens.get("expires_in", 3600)
    ga.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    await db.commit()
    return tokens["access_token"]

async def _synced_class_accounts(db: AsyncSession, class_id: int, flag: str) -> list[tuple[User, GoogleAccount]]:
    result = await db.execute(
        select(User, GoogleAccount)
        .join(GoogleAccount, GoogleAccount.user_id == User.id)
        .where(User.class_id == class_id, getattr(GoogleAccount, flag) == True)  # noqa: E712
    )
    return list(result.all())

# --- Calendar ---

async def sync_event_created(db: AsyncSession, event: CalendarEvent):
    accounts = await _synced_class_accounts(db, event.class_id, "sync_calendar")
    if not accounts:
        return
    body = google_client.calendar_event_body(event.title, event.description, event.date, event.end_date, event.time)
    for user, ga in accounts:
        try:
            token = await _valid_access_token(db, ga)
            g_event = await google_client.create_calendar_event(token, body)
            db.add(GoogleSyncedEvent(user_id=user.id, calendar_event_id=event.id, google_event_id=g_event["id"]))
            await db.commit()
        except Exception as e:
            logger.warning("Google Calendar sync (create) failed for user %s: %s", user.id, e)

async def sync_event_updated(db: AsyncSession, event: CalendarEvent):
    result = await db.execute(select(GoogleSyncedEvent).where(GoogleSyncedEvent.calendar_event_id == event.id))
    mappings = list(result.scalars().all())
    if not mappings:
        return
    body = google_client.calendar_event_body(event.title, event.description, event.date, event.end_date, event.time)
    for m in mappings:
        try:
            ga_res = await db.execute(select(GoogleAccount).where(GoogleAccount.user_id == m.user_id))
            ga = ga_res.scalar_one_or_none()
            if not ga or not ga.sync_calendar:
                continue
            token = await _valid_access_token(db, ga)
            await google_client.update_calendar_event(token, m.google_event_id, body)
        except Exception as e:
            logger.warning("Google Calendar sync (update) failed for user %s: %s", m.user_id, e)

async def sync_event_deleted(db: AsyncSession, event_id: int):
    result = await db.execute(select(GoogleSyncedEvent).where(GoogleSyncedEvent.calendar_event_id == event_id))
    mappings = list(result.scalars().all())
    for m in mappings:
        try:
            ga_res = await db.execute(select(GoogleAccount).where(GoogleAccount.user_id == m.user_id))
            ga = ga_res.scalar_one_or_none()
            if ga:
                token = await _valid_access_token(db, ga)
                await google_client.delete_calendar_event(token, m.google_event_id)
        except Exception as e:
            logger.warning("Google Calendar sync (delete) failed for user %s: %s", m.user_id, e)
        finally:
            await db.delete(m)
    await db.commit()

# --- Homework -> Tasks ---

async def sync_homework_created(db: AsyncSession, hw: Homework):
    accounts = await _synced_class_accounts(db, hw.class_id, "sync_homework_tasks")
    if not accounts:
        return
    for user, ga in accounts:
        try:
            token = await _valid_access_token(db, ga)
            g_task = await google_client.create_task(token, hw.description[:200] or "Hausaufgabe", hw.description, hw.due_date)
            db.add(GoogleSyncedTask(user_id=user.id, homework_id=hw.id, google_task_id=g_task["id"]))
            await db.commit()
        except Exception as e:
            logger.warning("Google Tasks sync (create) failed for user %s: %s", user.id, e)

async def sync_homework_deleted(db: AsyncSession, hw_id: int):
    result = await db.execute(select(GoogleSyncedTask).where(GoogleSyncedTask.homework_id == hw_id))
    mappings = list(result.scalars().all())
    for m in mappings:
        try:
            ga_res = await db.execute(select(GoogleAccount).where(GoogleAccount.user_id == m.user_id))
            ga = ga_res.scalar_one_or_none()
            if ga:
                token = await _valid_access_token(db, ga)
                await google_client.delete_task(token, m.google_task_id)
        except Exception as e:
            logger.warning("Google Tasks sync (delete) failed for user %s: %s", m.user_id, e)
        finally:
            await db.delete(m)
    await db.commit()
