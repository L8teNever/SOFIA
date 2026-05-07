from fastapi import APIRouter, Depends, Request
from backend.auth import get_current_user
from backend.models.user import User
from backend.schemas import UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user

@router.get("/check")
async def check(request: Request):
    from backend.config import settings
    email = settings.dev_email or request.headers.get("Cf-Access-Authenticated-User-Email")
    return {"authenticated": bool(email), "email": email}
