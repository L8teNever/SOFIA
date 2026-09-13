from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.database import get_db
from backend.auth import get_current_user, require_admin
from backend.models.meal_plan import MealPlan, MealPlanDay
from backend.models.user import User
from backend.schemas import MealPlanOut, MealPlanDayOut, MealPlanDayUpdate
from backend.config import settings
from backend.gemini_client import extract_meal_days, GeminiError
from backend.services.virus_scanner import scan_file
from backend.services.compression import compress_lossless
from backend.services.audit_service import log_audit
from datetime import date, datetime, timedelta
import os, uuid, aiofiles, asyncio, logging
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/mealplan", tags=["mealplan"])

# In-memory background processing state
upload_status = {
    "status": "idle",  # "idle" | "processing" | "ready" | "error"
    "message": "",
    "started_at": None,
    "error": None,
    "plan_id": None,
}


async def _process_meal_plan_background(raw: bytes, content_type: str, filename: str, user_id: int):
    global upload_status
    try:
        parsed_days = await extract_meal_days(raw, content_type)
        from backend.database import AsyncSessionLocal
        from backend.routes.vapid import push_to_users
        from backend.models.user import User

        async with AsyncSessionLocal() as db:
            plan = MealPlan(image_url=f"/uploads/mealplan/{filename}", uploaded_by=user_id)
            db.add(plan)
            await db.flush()
            for d in parsed_days:
                try:
                    if datetime.fromisoformat(d["date"]).weekday() >= 5:
                        continue
                except Exception:
                    pass
                db.add(MealPlanDay(plan_id=plan.id, date=d["date"], meal=d["meal"], edited=False))
            await db.commit()

            # Push notification to all users about the new meal plan
            try:
                res_users = await db.execute(select(User))
                all_users = res_users.scalars().all()
                if all_users:
                    await push_to_users(
                        db,
                        all_users,
                        "Neuer Speiseplan",
                        "Der neue Speiseplan für die Woche ist jetzt verfügbar.",
                    )
            except Exception as e:
                logger.warning("Konnte Push-Benachrichtigung für Speiseplan nicht versenden: %s", e)

            upload_status["status"] = "ready"
            upload_status["plan_id"] = plan.id
            upload_status["message"] = "Speiseplan erfolgreich erkannt!"
            upload_status["error"] = None
            logger.info("Speiseplan erfolgreich im Hintergrund verarbeitet (Plan ID %s)", plan.id)
    except GeminiError as e:
        logger.error("Hintergrund-Verarbeitung Speiseplan fehlgeschlagen: %s", e)
        upload_status["status"] = "error"
        upload_status["error"] = str(e)
        upload_status["message"] = f"Fehler bei KI-Erkennung: {e}"
    except Exception as e:
        logger.exception("Unerwarteter Fehler bei Hintergrund-Verarbeitung Speiseplan: %s", e)
        upload_status["status"] = "error"
        upload_status["error"] = "Unerwarteter Fehler bei der Bilderkennung"
        upload_status["message"] = "Unerwarteter Fehler bei der Bilderkennung"


async def _get_plan(db: AsyncSession, plan_id: int) -> Optional[MealPlan]:
    result = await db.execute(
        select(MealPlan).options(selectinload(MealPlan.days)).where(MealPlan.id == plan_id)
    )
    return result.scalar_one_or_none()


@router.post("/upload")
async def upload_meal_plan(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    global upload_status
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Nur Bilddateien erlaubt")
    raw = await file.read()
    if len(raw) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß (max. 1 GB)")

    # 1. Virenscanner
    await scan_file(raw, file.filename or "mealplan.jpg", file.content_type)

    # 2. Verlustfreie Komprimierung
    raw, ext, mime = compress_lossless(raw, file.filename or "", file.content_type)
    if not ext:
        ext = os.path.splitext(file.filename or "")[1] or ".jpg"

    # If already processing within the last 90 seconds, reject concurrent uploads
    if upload_status["status"] == "processing" and upload_status.get("started_at"):
        try:
            started = datetime.fromisoformat(upload_status["started_at"])
            if (datetime.now() - started).total_seconds() < 90:
                raise HTTPException(409, "Ein Speiseplan wird gerade bereits im Hintergrund verarbeitet.")
        except Exception:
            pass

    plan_dir = os.path.join(settings.upload_dir, "mealplan")
    os.makedirs(plan_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    async with aiofiles.open(os.path.join(plan_dir, filename), "wb") as out:
        await out.write(raw)

    upload_status["status"] = "processing"
    upload_status["message"] = "KI analysiert den Speiseplan im Hintergrund..."
    upload_status["started_at"] = datetime.now().isoformat()
    upload_status["error"] = None
    upload_status["plan_id"] = None

    await log_audit(
        db,
        action="mealplan.upload",
        user=current_user,
        entity_type="meal_plan",
        details={"filename": file.filename, "size": len(raw)},
        request=request,
    )

    asyncio.create_task(_process_meal_plan_background(raw, mime or file.content_type, filename, current_user.id))

    return {
        "ok": True,
        "status": "processing",
        "message": "Foto hochgeladen! Die KI verarbeitet den Plan im Hintergrund.",
    }


async def cleanup_past_mealplans(db: AsyncSession) -> dict:
    """
    Cleans up old meal plan image files from past weeks (older than the current week's Monday).
    Preserves text meal records in the database, but deletes the heavy images from disk.
    Homework files and attachments are strictly preserved and never touched.
    """
    today = date.today()
    this_monday_str = (today - timedelta(days=today.weekday())).isoformat()

    plans_res = await db.execute(select(MealPlan).options(selectinload(MealPlan.days)))
    all_plans = plans_res.scalars().all()

    cleaned_count = 0
    freed_bytes = 0

    for p in all_plans:
        # Determine if all days of this plan are strictly before this week's Monday
        dates = [d.date for d in p.days if d.date]
        if dates and max(dates) < this_monday_str and p.image_url:
            img_rel = p.image_url.lstrip("/")
            full_path = os.path.join(".", img_rel)
            if os.path.exists(full_path):
                try:
                    freed_bytes += os.path.getsize(full_path)
                    os.remove(full_path)
                    cleaned_count += 1
                except OSError:
                    pass
            p.image_url = ""  # Mark image removed but keep text meal days intact

    if cleaned_count > 0:
        await db.commit()

    return {"cleaned_files": cleaned_count, "freed_bytes": freed_bytes}


@router.get("/status")
async def meal_plan_status(current_user: User = Depends(get_current_user)):
    return upload_status


@router.get("/current")
async def current_meal_plan(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    today_str = today.isoformat()
    this_monday_str = this_monday.isoformat()

    # Find the most recently uploaded plan
    res_latest = await db.execute(
        select(MealPlan).order_by(MealPlan.created_at.desc()).limit(1)
    )
    latest_plan = res_latest.scalar_one_or_none()

    plan = None
    today_day = None

    if latest_plan:
        candidate_plan = await _get_plan(db, latest_plan.id)
        if candidate_plan and candidate_plan.days:
            created_recently = True
            if latest_plan.created_at:
                try:
                    cat = latest_plan.created_at.replace(tzinfo=None)
                    created_recently = (datetime.utcnow() - cat).days < 7
                except Exception:
                    created_recently = True

            max_date = max(d.date for d in candidate_plan.days)
            # Show plan if uploaded recently or covers current/upcoming dates
            if created_recently or max_date >= this_monday_str:
                plan = candidate_plan
                for d in plan.days:
                    if d.date == today_str:
                        today_day = d
                        break

    # Filter out Saturday and Sunday from the returned plan days safely
    if plan and plan.days:
        clean_days = []
        for d in plan.days:
            try:
                if datetime.fromisoformat(d.date).weekday() < 5:
                    clean_days.append(d)
            except Exception:
                clean_days.append(d)
        plan.days = clean_days

    return {
        "today": MealPlanDayOut.model_validate(today_day) if today_day else None,
        "plan": MealPlanOut.model_validate(plan) if plan else None,
        "status": upload_status["status"],
        "status_message": upload_status["message"],
        "status_error": upload_status["error"],
    }


@router.delete("/{plan_id}")
async def delete_meal_plan(
    plan_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin),
):
    plan = await _get_plan(db, plan_id)
    if not plan:
        raise HTTPException(404)
    path = os.path.join(settings.upload_dir, "mealplan", os.path.basename(plan.image_url))
    if os.path.exists(path):
        os.remove(path)
    await db.delete(plan)
    await db.commit()
    return {"ok": True}


@router.patch("/day/{day_id}", response_model=MealPlanDayOut)
async def update_meal_plan_day(
    day_id: int, data: MealPlanDayUpdate,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(MealPlanDay).where(MealPlanDay.id == day_id))
    day = result.scalar_one_or_none()
    if not day:
        raise HTTPException(404)
    day.meal = data.meal
    day.edited = True
    await db.commit()
    await db.refresh(day)
    return day
