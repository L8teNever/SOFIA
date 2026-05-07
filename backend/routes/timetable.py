from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.config import settings
from cryptography.fernet import Fernet
import httpx
from datetime import date, timedelta

router = APIRouter(prefix="/api/v1/timetable", tags=["timetable"])

def decrypt_password(enc: str) -> str:
    key = settings.encryption_key
    if not key:
        return enc
    return Fernet(key.encode()).decrypt(enc.encode()).decode()

@router.get("/")
async def get_timetable(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        raise HTTPException(400, "No class assigned")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == current_user.class_id))
    cls = result.scalar_one_or_none()
    if not cls or not cls.untis_url:
        return {"configured": False, "lessons": []}

    try:
        password = decrypt_password(cls.untis_password_enc) if cls.untis_password_enc else ""
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)

        base = cls.untis_url.rstrip("/")
        school = cls.untis_school
        async with httpx.AsyncClient(timeout=10) as client:
            login = await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "1", "method": "authenticate", "params": {"user": cls.untis_user, "password": password, "client": "sofia"}, "jsonrpc": "2.0"}
            )
            login_data = login.json()
            if "error" in login_data:
                return {"configured": True, "error": "Login failed", "lessons": []}
            session_id = login_data["result"]["sessionId"]
            cookies = {"JSESSIONID": session_id}

            tt_resp = await client.get(
                f"{base}/WebUntis/api/public/timetable/weekly/pageconfig?type=1&id=0&date={monday.strftime('%Y-%m-%d')}&formatId=1",
                cookies=cookies, headers={"school": school}
            )
            lessons_resp = await client.get(
                f"{base}/WebUntis/api/public/timetable/weekly/data?elementType=1&elementId=0&date={monday.strftime('%Y-%m-%d')}&formatId=1",
                cookies=cookies, headers={"school": school}
            )
            await client.post(f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "2", "method": "logout", "params": {}, "jsonrpc": "2.0"}, cookies=cookies)

        return {"configured": True, "lessons": lessons_resp.json() if lessons_resp.status_code == 200 else [], "week_start": monday.isoformat()}
    except Exception as e:
        return {"configured": True, "error": str(e), "lessons": []}
