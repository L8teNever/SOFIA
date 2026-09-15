"""Thin Google OAuth2 + Calendar/Tasks API client, in the same spirit as
gemini_client.py — plain httpx calls instead of pulling in Google's SDK for
what's a handful of REST endpoints."""
from backend.config import settings
from datetime import date, datetime, timedelta, timezone
import httpx, logging

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
TASKS_URL = "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks"

SCOPES = "openid email https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/tasks"

class GoogleAuthError(Exception):
    pass

def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret and settings.google_redirect_uri)

def build_auth_url(state: str) -> str:
    if not is_configured():
        raise GoogleAuthError("Google-Anmeldung ist nicht konfiguriert (GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI fehlen)")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{httpx.QueryParams(params)}"

async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        })
    if resp.status_code != 200:
        logger.warning("Google token exchange failed (%s): %s", resp.status_code, resp.text[:300])
        raise GoogleAuthError("Google-Anmeldung fehlgeschlagen")
    return resp.json()

async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        })
    if resp.status_code != 200:
        logger.warning("Google token refresh failed (%s): %s", resp.status_code, resp.text[:300])
        raise GoogleAuthError("Google-Token konnte nicht erneuert werden")
    return resp.json()

async def get_userinfo(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        raise GoogleAuthError("Google-Kontoinfo konnte nicht gelesen werden")
    return resp.json()

def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

def calendar_event_body(title: str, description: str | None, event_date: str, end_date: str | None, time_str: str | None) -> dict:
    body = {"summary": title, "description": description or ""}
    if time_str:
        start_dt = f"{event_date}T{time_str}:00"
        end_dt_date = end_date or event_date
        end_dt = f"{end_dt_date}T{time_str}:00"
        # SOFIA doesn't store an end time — give a timed event a sensible
        # 1-hour default length rather than a zero-length start==end event.
        start_obj = datetime.fromisoformat(start_dt)
        end_obj = datetime.fromisoformat(end_dt)
        if end_obj <= start_obj:
            end_obj = start_obj + timedelta(hours=1)
        body["start"] = {"dateTime": start_obj.isoformat(), "timeZone": "Europe/Berlin"}
        body["end"] = {"dateTime": end_obj.isoformat(), "timeZone": "Europe/Berlin"}
    else:
        # All-day event — Google's all-day "end.date" is EXCLUSIVE (the day
        # after the last day shown), unlike SOFIA's own inclusive end_date.
        last_day = date.fromisoformat(end_date or event_date)
        body["start"] = {"date": event_date}
        body["end"] = {"date": (last_day + timedelta(days=1)).isoformat()}
    return body

async def create_calendar_event(access_token: str, event_body: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(CALENDAR_EVENTS_URL, headers=_auth_headers(access_token), json=event_body)
    if resp.status_code not in (200, 201):
        logger.warning("Google Calendar create failed (%s): %s", resp.status_code, resp.text[:300])
        raise GoogleAuthError("Termin konnte nicht in Google Kalender angelegt werden")
    return resp.json()

async def update_calendar_event(access_token: str, google_event_id: str, event_body: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.put(f"{CALENDAR_EVENTS_URL}/{google_event_id}", headers=_auth_headers(access_token), json=event_body)
    if resp.status_code not in (200, 201):
        logger.warning("Google Calendar update failed (%s): %s", resp.status_code, resp.text[:300])
        raise GoogleAuthError("Termin konnte nicht in Google Kalender aktualisiert werden")
    return resp.json()

async def delete_calendar_event(access_token: str, google_event_id: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.delete(f"{CALENDAR_EVENTS_URL}/{google_event_id}", headers=_auth_headers(access_token))
    # 404/410 means it's already gone on Google's side — fine, nothing left to delete.
    if resp.status_code not in (200, 204, 404, 410):
        logger.warning("Google Calendar delete failed (%s): %s", resp.status_code, resp.text[:300])
        raise GoogleAuthError("Termin konnte nicht aus Google Kalender gelöscht werden")

async def create_task(access_token: str, title: str, notes: str | None, due_date: str | None) -> dict:
    body = {"title": title, "notes": notes or ""}
    if due_date:
        # Google Tasks' "due" is a full RFC3339 timestamp but only the date
        # part is actually used/shown for a task's due date.
        body["due"] = f"{due_date}T00:00:00.000Z"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(TASKS_URL, headers=_auth_headers(access_token), json=body)
    if resp.status_code not in (200, 201):
        logger.warning("Google Tasks create failed (%s): %s", resp.status_code, resp.text[:300])
        raise GoogleAuthError("Aufgabe konnte nicht in Google Tasks angelegt werden")
    return resp.json()

async def delete_task(access_token: str, google_task_id: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.delete(f"{TASKS_URL}/{google_task_id}", headers=_auth_headers(access_token))
    if resp.status_code not in (200, 204, 404):
        logger.warning("Google Tasks delete failed (%s): %s", resp.status_code, resp.text[:300])
        raise GoogleAuthError("Aufgabe konnte nicht aus Google Tasks gelöscht werden")
