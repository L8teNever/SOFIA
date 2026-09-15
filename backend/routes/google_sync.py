from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.user import User
from backend.models.google_account import GoogleAccount
from backend.schemas import GoogleStatusOut, GoogleSettingsUpdate
from backend.services.crypto import encrypt_value
from backend import google_client
from datetime import datetime, timedelta, timezone
import secrets, logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/google", tags=["google"])

# In-memory CSRF-state per user, same "generous, single-process" pragmatism
# as backend/services/rate_limiter.py — SOFIA's own auth is delegated to
# Cloudflare Access (see backend/auth.py), so the callback already can't be
# reached by anyone but the authenticated user; this is just the standard
# OAuth state check on top of that.
_pending_states: dict[int, str] = {}

@router.get("/status", response_model=GoogleStatusOut)
async def google_status(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(GoogleAccount).where(GoogleAccount.user_id == current_user.id))
    ga = result.scalar_one_or_none()
    return GoogleStatusOut(
        configured=google_client.is_configured(),
        connected=ga is not None,
        email=ga.google_email if ga else None,
        sync_calendar=ga.sync_calendar if ga else False,
        sync_homework_tasks=ga.sync_homework_tasks if ga else False,
    )

@router.get("/auth-url")
async def google_auth_url(current_user: User = Depends(get_current_user)):
    if not google_client.is_configured():
        raise HTTPException(400, "Google-Anmeldung ist serverseitig nicht konfiguriert")
    state = secrets.token_urlsafe(24)
    _pending_states[current_user.id] = state
    return {"url": google_client.build_auth_url(state)}

@router.get("/callback")
async def google_callback(code: str = "", state: str = "", error: str = "",
                           db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if error:
        return RedirectResponse(f"/google-sync?google_error={error}")
    if not code or _pending_states.get(current_user.id) != state:
        return RedirectResponse("/google-sync?google_error=state_mismatch")
    _pending_states.pop(current_user.id, None)

    try:
        tokens = await google_client.exchange_code(code)
        userinfo = await google_client.get_userinfo(tokens["access_token"])
    except google_client.GoogleAuthError as e:
        logger.warning("Google OAuth callback failed for user %s: %s", current_user.id, e)
        return RedirectResponse("/google-sync?google_error=exchange_failed")

    result = await db.execute(select(GoogleAccount).where(GoogleAccount.user_id == current_user.id))
    ga = result.scalar_one_or_none()
    if not ga:
        ga = GoogleAccount(user_id=current_user.id, access_token_enc="", refresh_token_enc="")
        db.add(ga)

    ga.google_email = userinfo.get("email")
    ga.access_token_enc = encrypt_value(tokens["access_token"])
    # Google only returns a refresh_token on the FIRST consent (or when
    # prompt=consent forces it, which build_auth_url always sets) — keep the
    # old one if a re-auth somehow didn't include a new one.
    if tokens.get("refresh_token"):
        ga.refresh_token_enc = encrypt_value(tokens["refresh_token"])
    ga.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))

    await db.commit()
    return RedirectResponse("/google-sync?google_connected=1")

@router.put("/settings", response_model=GoogleStatusOut)
async def update_google_settings(data: GoogleSettingsUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(GoogleAccount).where(GoogleAccount.user_id == current_user.id))
    ga = result.scalar_one_or_none()
    if not ga:
        raise HTTPException(400, "Kein Google-Konto verbunden")
    if data.sync_calendar is not None:
        ga.sync_calendar = data.sync_calendar
    if data.sync_homework_tasks is not None:
        ga.sync_homework_tasks = data.sync_homework_tasks
    await db.commit()
    return GoogleStatusOut(
        configured=google_client.is_configured(), connected=True, email=ga.google_email,
        sync_calendar=ga.sync_calendar, sync_homework_tasks=ga.sync_homework_tasks,
    )

@router.delete("/disconnect")
async def disconnect_google(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(GoogleAccount).where(GoogleAccount.user_id == current_user.id))
    ga = result.scalar_one_or_none()
    if ga:
        await db.delete(ga)
        await db.commit()
    return {"ok": True}
