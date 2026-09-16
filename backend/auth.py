from fastapi import Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from backend.database import get_db
from backend.models.user import User, UserRole
from backend.models.user_email_alias import UserEmailAlias
from backend.config import settings
import secrets

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    email = settings.dev_email or request.headers.get("Cf-Access-Authenticated-User-Email")

    # Server-to-server auth for the MCP bridge container: it calls this API
    # over the private Docker network (never through the public Cloudflare
    # hostname, so it never has a Cf-Access header) and proves itself with a
    # shared secret instead, naming which SOFIA user to act as. Only trusted
    # when the token matches exactly — compare_digest to avoid leaking the
    # correct value one byte at a time through response-timing differences.
    if not email:
        internal_token = request.headers.get("X-Internal-Token")
        if internal_token and settings.internal_service_token and secrets.compare_digest(internal_token, settings.internal_service_token):
            email = request.headers.get("X-Act-As-Email") or settings.mcp_default_user_email

    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        # Secondary email addresses a super-admin registered for this
        # account (e.g. a second Cloudflare Access login) — resolve to the
        # same underlying user instead of falling through to "not
        # activated" below.
        alias_result = await db.execute(select(UserEmailAlias).where(UserEmailAlias.email == email))
        alias = alias_result.scalar_one_or_none()
        if alias:
            owner_result = await db.execute(select(User).where(User.id == alias.user_id))
            user = owner_result.scalar_one_or_none()

    if user is None:
        count = (await db.execute(select(func.count()).select_from(User))).scalar()
        if count == 0:
            user = User(email=email, role=UserRole.super_admin, display_name=email.split("@")[0])
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            raise HTTPException(status_code=403, detail="Account not activated")

    return user

async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.admin, UserRole.super_admin):
        raise HTTPException(status_code=403, detail="Admin required")
    return user

async def require_super_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.super_admin:
        raise HTTPException(status_code=403, detail="Super-admin required")
    return user

def get_email_from_request(request: Request) -> str:
    if settings.dev_email:
        return settings.dev_email
    return request.headers.get("Cf-Access-Authenticated-User-Email", "")
