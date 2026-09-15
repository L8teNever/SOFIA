import json
import logging
from typing import Optional, Any
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.audit_log import AuditLog
from backend.models.user import User

logger = logging.getLogger(__name__)

def _get_client_ip(request: Optional[Request]) -> Optional[str]:
    if not request:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None

def _get_user_agent(request: Optional[Request]) -> Optional[str]:
    if not request:
        return None
    return request.headers.get("user-agent")

async def log_audit(
    db: AsyncSession,
    action: str,
    user: Optional[User] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[Any] = None,
    request: Optional[Request] = None,
) -> None:
    """
    Persistently records an action into the audit_logs table for Super-Admin inspection.
    Audit logs are permanently kept and never automatically deleted.
    """
    try:
        detail_str = None
        if details is not None:
            if isinstance(details, (dict, list)):
                detail_str = json.dumps(details, ensure_ascii=False)
            else:
                detail_str = str(details)

        ip = _get_client_ip(request)
        ua = _get_user_agent(request)

        user_id = user.id if user else None
        username = (user.display_name or user.email) if user else None
        user_role = user.role.value if (user and hasattr(user.role, "value")) else (str(user.role) if user else None)

        log_entry = AuditLog(
            user_id=user_id,
            username=username,
            user_role=user_role,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=detail_str,
            ip_address=ip,
            user_agent=ua,
        )
        db.add(log_entry)
        # commit (not just flush) — this is very often the LAST thing a
        # route does, so a mere flush only survives if some unrelated later
        # commit in the same request happens to follow it. Confirmed in
        # production: only calendar.event_create (and one homework.create)
        # ever showed up, both purely because a notification call right
        # after them commits its own row and incidentally flushed this one
        # along with it — every other action (updates, deletes, admin
        # actions, ...) was silently dropped when the session closed at
        # request end with this row still uncommitted.
        await db.commit()
        logger.info("AUDIT: [%s] User=%s Action=%s Entity=%s:%s", ip or "local", username or "anon", action, entity_type, entity_id)
    except Exception as e:
        logger.error("Fehler beim Erstellen des Audit-Logs: %s", e)
