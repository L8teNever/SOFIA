from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, delete, func
from backend.database import get_db, AsyncSessionLocal
from backend.auth import get_current_user, require_super_admin, require_admin
from backend.models.class_group import ClassGroup
from backend.models.subject import Subject
from backend.models.manual_timetable_entry import ManualTimetableEntry
from backend.models.user import User
from backend.schemas import ClassGroupOut, ClassGroupCreate
from backend.config import settings
from backend.gemini_client import extract_timetable, GeminiError
from backend.services.virus_scanner import scan_file
from backend.services.audit_service import log_audit
from cryptography.fernet import Fernet
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime, timedelta
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

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
    password: str = ""

@router.post("/{class_id}/untis")
async def save_untis(class_id: int, data: UntisCredentials, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    if current_user.role != "super_admin" and current_user.class_id != class_id:
        raise HTTPException(403, "Not your class")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    f = get_fernet()
    cls.untis_url    = data.url
    cls.untis_school = data.school
    cls.untis_class  = data.class_name
    cls.untis_user   = data.username
    if data.password:  # only overwrite if a new password was entered
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
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
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

@router.post("/{class_id}/untis/reconnect")
async def reconnect_untis(class_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    if current_user.role != "super_admin" and current_user.class_id != class_id:
        raise HTTPException(403, "Not your class")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls or not cls.untis_url:
        return {"ok": False, "message": "Untis nicht konfiguriert"}
    f = get_fernet()
    password = f.decrypt(cls.untis_password_enc.encode()).decode() if (f and cls.untis_password_enc) else (cls.untis_password_enc or "")
    base = cls.untis_url.rstrip("/")
    school = cls.untis_school
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
                json={"id": "1", "method": "authenticate",
                      "params": {"user": cls.untis_user, "password": password, "client": "sofia"},
                      "jsonrpc": "2.0"}
            )
            body = resp.json()
            if "error" in body:
                return {"ok": False, "message": body["error"].get("message", "Login fehlgeschlagen")}
            session_id = body.get("result", {}).get("sessionId")
            await client.post(
                f"{base}/WebUntis/jsonrpc.do?school={school}",
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

    try:
        from backend.routes.timetable import _fetch_timetable, _strip_server  # noqa
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        end_date = monday + timedelta(weeks=8)
        server = _strip_server(cls.untis_url)

        loop = asyncio.get_event_loop()
        lessons = await loop.run_in_executor(
            None, _fetch_timetable, server, cls.untis_school,
            cls.untis_user, password, cls.untis_class or "",
            monday, end_date,
        )

        seen = {}
        for l in lessons:
            short = l.get("subject_short", "").strip()
            long_name = l.get("subject", "").strip() or short
            if short and short not in seen:
                seen[short] = long_name

        # Load existing short_names for this class
        existing = await db.execute(select(Subject).where(Subject.class_id == class_id))
        existing_shorts = {s.short_name.upper() for s in existing.scalars().all() if s.short_name}

        imported = skipped = 0
        for i, (short, long_name) in enumerate(seen.items()):
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


# ---------------------------------------------------------------------------
# Photo-recognized ("manual") timetable — fallback for when a class can't
# connect via WebUntis. Admin uploads a photo of the printed Stundenplan,
# Gemini reads it into weekday/time/subject entries, subjects are matched
# onto existing Subject rows (or created) so homework/grades keep working
# unchanged, and the class can switch between 'untis' and 'manual' anytime.
# ---------------------------------------------------------------------------

_tt_upload_status: dict[int, dict] = {}

def _tt_status_for(class_id: int) -> dict:
    return _tt_upload_status.setdefault(class_id, {"status": "idle", "message": "", "started_at": None, "error": None})

def _check_class_access(current_user: User, class_id: int):
    if current_user.role != "super_admin" and current_user.class_id != class_id:
        raise HTTPException(403, "Not your class")

async def _resolve_subject(db: AsyncSession, class_id: int, existing: list[Subject], name: str, short: str, color_i: int) -> tuple[int, list[Subject]]:
    name_l, short_l = name.lower(), short.lower()
    for s in existing:
        if (s.name or "").lower() == name_l or (short_l and (s.short_name or "").lower() == short_l):
            return s.id, existing
    subj = Subject(name=name, short_name=short or None, color=SUBJECT_COLORS[color_i % len(SUBJECT_COLORS)], class_id=class_id, is_global=False)
    db.add(subj)
    await db.flush()
    existing.append(subj)
    return subj.id, existing

async def _process_timetable_photo(raw: bytes, content_type: str, class_id: int):
    st = _tt_status_for(class_id)
    try:
        entries = await extract_timetable(raw, content_type)
        async with AsyncSessionLocal() as db:
            await db.execute(delete(ManualTimetableEntry).where(ManualTimetableEntry.class_id == class_id))

            subj_res = await db.execute(
                select(Subject).where(or_(Subject.class_id == class_id, Subject.is_global == True, Subject.class_id.is_(None)))
            )
            existing_subjects = list(subj_res.scalars().all())

            subject_cache: dict[str, int] = {}
            color_i = 0
            for e in entries:
                cache_key = e["subject"].lower() + "|" + e["subject_short"].lower()
                if cache_key not in subject_cache:
                    subj_id, existing_subjects = await _resolve_subject(
                        db, class_id, existing_subjects, e["subject"], e["subject_short"], color_i
                    )
                    subject_cache[cache_key] = subj_id
                    color_i += 1
                db.add(ManualTimetableEntry(
                    class_id=class_id, weekday=e["weekday"], start_time=e["start_time"],
                    end_time=e["end_time"], subject_id=subject_cache[cache_key], room=e["room"],
                ))

            cls_res = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
            cls = cls_res.scalar_one_or_none()
            if cls:
                cls.timetable_source = "manual"
            await db.commit()

        st["status"] = "ready"
        st["message"] = f"{len(entries)} Unterrichtsstunden erkannt!"
        st["error"] = None
        logger.info("Foto-Stundenplan erfolgreich verarbeitet (Klasse %s, %s Einträge)", class_id, len(entries))
    except GeminiError as e:
        logger.error("Hintergrund-Verarbeitung Stundenplan fehlgeschlagen: %s", e)
        st["status"] = "error"
        st["error"] = str(e)
        st["message"] = f"Fehler bei KI-Erkennung: {e}"
    except Exception as e:
        logger.exception("Unerwarteter Fehler bei Hintergrund-Verarbeitung Stundenplan: %s", e)
        st["status"] = "error"
        st["error"] = "Unerwarteter Fehler bei der Bilderkennung"
        st["message"] = "Unerwarteter Fehler bei der Bilderkennung"


@router.post("/{class_id}/timetable-photo/upload")
async def upload_timetable_photo(
    class_id: int, request: Request, file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin),
):
    _check_class_access(current_user, class_id)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Nur Bilddateien erlaubt")
    raw = await file.read()
    if len(raw) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")

    await scan_file(raw, file.filename or "stundenplan.jpg", file.content_type)

    st = _tt_status_for(class_id)
    if st["status"] == "processing" and st.get("started_at"):
        try:
            started = datetime.fromisoformat(st["started_at"])
            if (datetime.now() - started).total_seconds() < 90:
                raise HTTPException(409, "Ein Stundenplan wird gerade bereits im Hintergrund verarbeitet.")
        except HTTPException:
            raise
        except Exception:
            pass

    st["status"] = "processing"
    st["message"] = "KI erkennt den Stundenplan im Hintergrund…"
    st["started_at"] = datetime.now().isoformat()
    st["error"] = None

    await log_audit(
        db, action="timetable.upload_photo", user=current_user, entity_type="class_group",
        details={"filename": file.filename, "size": len(raw), "class_id": class_id},
        request=request,
    )

    asyncio.create_task(_process_timetable_photo(raw, file.content_type, class_id))

    return {"ok": True, "status": "processing", "message": "Foto hochgeladen! Die KI erkennt den Stundenplan im Hintergrund."}


@router.get("/{class_id}/timetable-photo/status")
async def timetable_photo_status(class_id: int, current_user: User = Depends(require_admin)):
    _check_class_access(current_user, class_id)
    return _tt_status_for(class_id)


class TimetableSourceUpdate(BaseModel):
    source: str  # 'untis' | 'manual'

@router.get("/{class_id}/timetable-source")
async def get_timetable_source(class_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    _check_class_access(current_user, class_id)
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    count_res = await db.execute(select(func.count()).select_from(ManualTimetableEntry).where(ManualTimetableEntry.class_id == class_id))
    manual_count = count_res.scalar() or 0
    return {
        "source": cls.timetable_source or "untis",
        "has_untis": bool(cls.untis_url),
        "has_manual": manual_count > 0,
        "manual_entry_count": manual_count,
    }

@router.post("/{class_id}/timetable-source")
async def set_timetable_source(class_id: int, data: TimetableSourceUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    _check_class_access(current_user, class_id)
    if data.source not in ("untis", "manual"):
        raise HTTPException(400, "Ungültige Quelle")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    cls = result.scalar_one_or_none()
    if not cls:
        raise HTTPException(404)
    if data.source == "untis" and not cls.untis_url:
        raise HTTPException(400, "Untis ist für diese Klasse nicht konfiguriert")
    if data.source == "manual":
        count_res = await db.execute(select(func.count()).select_from(ManualTimetableEntry).where(ManualTimetableEntry.class_id == class_id))
        if (count_res.scalar() or 0) == 0:
            raise HTTPException(400, "Kein Foto-Stundenplan vorhanden")
    cls.timetable_source = data.source
    await db.commit()
    return {"ok": True, "source": data.source}


# ---------------------------------------------------------------------------
# Manual editing of individual timetable entries — lets an admin fix up or
# build out the manual/photo timetable by hand (correcting a misread entry,
# or adding one from scratch) without needing a fresh photo each time.
# ---------------------------------------------------------------------------

class ManualTimetableEntryIn(BaseModel):
    weekday: int      # 0=Montag .. 4=Freitag
    start_time: str   # "HH:MM"
    end_time: str     # "HH:MM"
    subject_id: int
    room: Optional[str] = None

def _hhmm_to_int(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 100 + int(m)

def _int_to_hhmm(v: int) -> str:
    return f"{v // 100:02d}:{v % 100:02d}"

async def _validate_manual_entry(db: AsyncSession, class_id: int, data: ManualTimetableEntryIn):
    if not (0 <= data.weekday <= 4):
        raise HTTPException(400, "Ungültiger Wochentag")
    try:
        start, end = _hhmm_to_int(data.start_time), _hhmm_to_int(data.end_time)
    except Exception:
        raise HTTPException(400, "Ungültige Uhrzeit")
    if end <= start:
        raise HTTPException(400, "Endzeit muss nach der Startzeit liegen")
    result = await db.execute(select(Subject).where(
        Subject.id == data.subject_id,
        or_(Subject.class_id == class_id, Subject.is_global == True, Subject.class_id.is_(None)),
    ))
    if not result.scalar_one_or_none():
        raise HTTPException(400, "Fach nicht gefunden")

@router.get("/{class_id}/timetable-manual")
async def list_manual_entries(class_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    _check_class_access(current_user, class_id)
    result = await db.execute(
        select(ManualTimetableEntry, Subject)
        .join(Subject, ManualTimetableEntry.subject_id == Subject.id)
        .where(ManualTimetableEntry.class_id == class_id)
        .order_by(ManualTimetableEntry.weekday, ManualTimetableEntry.start_time)
    )
    return [
        {
            "id": e.id, "weekday": e.weekday,
            "start_time": _int_to_hhmm(e.start_time), "end_time": _int_to_hhmm(e.end_time),
            "subject_id": e.subject_id, "subject_name": s.name, "subject_short": s.short_name,
            "room": e.room,
        }
        for e, s in result.all()
    ]

@router.post("/{class_id}/timetable-manual")
async def create_manual_entry(class_id: int, data: ManualTimetableEntryIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    _check_class_access(current_user, class_id)
    await _validate_manual_entry(db, class_id, data)
    entry = ManualTimetableEntry(
        class_id=class_id, weekday=data.weekday,
        start_time=_hhmm_to_int(data.start_time), end_time=_hhmm_to_int(data.end_time),
        subject_id=data.subject_id, room=data.room,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {"ok": True, "id": entry.id}

@router.put("/{class_id}/timetable-manual/{entry_id}")
async def update_manual_entry(class_id: int, entry_id: int, data: ManualTimetableEntryIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    _check_class_access(current_user, class_id)
    await _validate_manual_entry(db, class_id, data)
    result = await db.execute(select(ManualTimetableEntry).where(
        ManualTimetableEntry.id == entry_id, ManualTimetableEntry.class_id == class_id
    ))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404)
    entry.weekday = data.weekday
    entry.start_time = _hhmm_to_int(data.start_time)
    entry.end_time = _hhmm_to_int(data.end_time)
    entry.subject_id = data.subject_id
    entry.room = data.room
    await db.commit()
    return {"ok": True}

@router.delete("/{class_id}/timetable-manual/{entry_id}")
async def delete_manual_entry(class_id: int, entry_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    _check_class_access(current_user, class_id)
    result = await db.execute(select(ManualTimetableEntry).where(
        ManualTimetableEntry.id == entry_id, ManualTimetableEntry.class_id == class_id
    ))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404)
    await db.delete(entry)
    await db.commit()
    return {"ok": True}
