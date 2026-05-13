from fastapi import Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from backend.database import get_db
from backend.models.user import User, UserRole
from backend.config import settings

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    email = settings.dev_email or request.headers.get("Cf-Access-Authenticated-User-Email")

    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

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
