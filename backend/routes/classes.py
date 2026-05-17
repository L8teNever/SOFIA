from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_super_admin, require_admin
from backend.models.class_group import ClassGroup
from backend.models.subject import Subject
from backend.models.user import User
from backend.schemas import ClassGroupOut, ClassGroupCreate
from backend.config import settings
from cryptography.fernet import Fernet
from typing import List, Optional
from pydantic import BaseModel
import httpx

router = APIRouter(prefix="/api/v1/classes", tags=["classes"])

def get_fernet():
    key = settings.encryption_key
    if not key:
        return None
    return Fernet(key.encode())

@router.get("/", response_model=List[ClassGroupOut])
async def list_classes(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    result = await db.execute(select(ClassGroup))
    return result.scalars().all()

@router.post("/", response_model=ClassGroupOut)
async def create_class(data: ClassGroupCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    cls = ClassGroup(name=data.name)
    db.add(cls)
    await db.commit()
    await db.refresh(cls)
    return cls

@router.delete("/{class_id}")
async def delete_class(class_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404, "Class not found")
    await db.delete(cls)
    await db.commit()
    return {"ok": True}

class UntisCredentials(BaseModel):
    url: str
    school: str
    class_name: str
    username: str
    password: str

@router.post("/{class_id}/untis")
async def save_untis(class_id: int, data: UntisCredentials, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    if current_user.role != "super_admin" and current_user.class_id != class_id:
        raise HTTPException(403, "Not your class")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    f = get_fernet()
    cls.untis_url = data.url
    cls.untis_school = data.school
    cls.untis_class = data.class_name
    cls.untis_user = data.username
    cls.untis_password_enc = f.encrypt(data.password.encode()).decode() if f else data.password
    await db.commit()
    return {"ok": True}

@router.get("/{class_id}/untis")
async def get_untis_status(class_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    return {
        "configured": bool(cls.untis_url),
        "url": cls.untis_url,
        "school": cls.untis_school,
        "class_name": cls.untis_class,
        "username": cls.untis_user,
    }

@router.post("/{class_id}/untis/test")
async def test_untis(class_id: int, data: UntisCredentials, _: User = Depends(require_admin)):
    base = data.url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={data.school}",
                json={"id": "1", "method": "authenticate",
                      "params": {"user": data.username, "password": data.password, "client": "sofia"},
                      "jsonrpc": "2.0"}
            )
            body = resp.json()
            if "error" in body:
                msg = body["error"].get("message", "Login fehlgeschlagen")
                return {"ok": False, "message": msg}
            session_id = body.get("result", {}).get("sessionId")
            # Logout immediately
            await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={data.school}",
                json={"id": "2", "method": "logout", "params": {}, "jsonrpc": "2.0"},
                cookies={"JSESSIONID": session_id}
            )
            return {"ok": True, "message": "Verbindung erfolgreich"}
    except httpx.ConnectError:
        return {"ok": False, "message": "Server nicht erreichbar"}
    except Exception as e:
        return {"ok": False, "message": str(e)}

SUBJECT_COLORS = ['#eaddff','#d3e3fd','#c4eed0','#ffdec1','#ffd8e4','#e8def8','#cfe2ff','#fce4ec','#e8f5e9','#fff3e0']

@router.post("/{class_id}/untis/import-subjects")
async def import_untis_subjects(class_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    if current_user.role != "super_admin" and current_user.class_id != class_id:
        raise HTTPException(403, "Not your class")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls or not cls.untis_url:
        raise HTTPException(400, "Untis nicht konfiguriert")

    f = get_fernet()
    password = f.decrypt(cls.untis_password_enc.encode()).decode() if (f and cls.untis_password_enc) else (cls.untis_password_enc or "")
    base = cls.untis_url.rstrip("/")
    school = cls.untis_school

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            login = await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "1", "method": "authenticate",
                      "params": {"user": cls.untis_user, "password": password, "client": "sofia"},
                      "jsonrpc": "2.0"}
            )
            login_data = login.json()
            if "error" in login_data:
                raise HTTPException(400, "Untis-Login fehlgeschlagen")
            session_id = login_data["result"]["sessionId"]
            cookies = {"JSESSIONID": session_id}

            subj_resp = await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "2", "method": "getSubjects", "params": {}, "jsonrpc": "2.0"},
                cookies=cookies,
            )
            await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "3", "method": "logout", "params": {}, "jsonrpc": "2.0"},
                cookies=cookies,
            )

        untis_subjects = subj_resp.json().get("result", [])

        # Load existing short_names for this class
        existing = await db.execute(select(Subject).where(Subject.class_id == class_id))
        existing_shorts = {s.short_name.upper() for s in existing.scalars().all() if s.short_name}

        imported = skipped = 0
        for i, s in enumerate(untis_subjects):
            short = (s.get("name") or "").strip()
            long_name = (s.get("longName") or "").strip() or short
            if not short:
                continue
            if short.upper() in existing_shorts:
                skipped += 1
                continue
            color = SUBJECT_COLORS[i % len(SUBJECT_COLORS)]
            db.add(Subject(name=long_name, short_name=short, color=color, class_id=class_id, is_global=False))
            existing_shorts.add(short.upper())
            imported += 1

        await db.commit()
        return {"ok": True, "imported": imported, "skipped": skipped}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
