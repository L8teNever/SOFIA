from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
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
from datetime import date, datetime, timedelta
from typing import Optional
import os, uuid, aiofiles

router = APIRouter(prefix="/api/v1/mealplan", tags=["mealplan"])


async def _get_plan(db: AsyncSession, plan_id: int) -> Optional[MealPlan]:
    result = await db.execute(
        select(MealPlan).options(selectinload(MealPlan.days)).where(MealPlan.id == plan_id)
    )
    return result.scalar_one_or_none()


@router.post("/upload", response_model=MealPlanOut)
async def upload_meal_plan(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Nur Bilddateien erlaubt")
    raw = await file.read()
    if len(raw) > settings.max_file_size:
        raise HTTPException(413, "Datei zu groß")

    try:
        parsed_days = await extract_meal_days(raw, file.content_type)
    except GeminiError as e:
        raise HTTPException(422, str(e))

    plan_dir = os.path.join(settings.upload_dir, "mealplan")
    os.makedirs(plan_dir, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    async with aiofiles.open(os.path.join(plan_dir, filename), "wb") as out:
        await out.write(raw)

    plan = MealPlan(image_url=f"/uploads/mealplan/{filename}", uploaded_by=current_user.id)
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
    return await _get_plan(db, plan.id)


@router.get("/current")
async def current_meal_plan(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    this_sunday = this_monday + timedelta(days=6)
    today_str = today.isoformat()
    this_monday_str = this_monday.isoformat()
    this_sunday_str = this_sunday.isoformat()

    # Find plans covering the current week (Monday - Sunday)
    result = await db.execute(
        select(MealPlanDay)
        .join(MealPlan)
        .where(MealPlanDay.date >= this_monday_str, MealPlanDay.date <= this_sunday_str)
        .order_by(MealPlan.created_at.desc())
    )
    current_week_days = result.scalars().all()

    plan_id = None
    today_day = None

    if current_week_days:
        plan_id = current_week_days[0].plan_id
        if today.weekday() < 5:
            for d in current_week_days:
                if d.plan_id == plan_id and d.date == today_str:
                    today_day = d
                    break
    elif today.weekday() >= 5:
        # On weekends (Sat/Sun), check if next week's plan was already uploaded
        next_monday = this_monday + timedelta(days=7)
        next_sunday = this_monday + timedelta(days=13)
        res_next = await db.execute(
            select(MealPlanDay)
            .join(MealPlan)
            .where(MealPlanDay.date >= next_monday.isoformat(), MealPlanDay.date <= next_sunday.isoformat())
            .order_by(MealPlan.created_at.desc())
            .limit(1)
        )
        next_day = res_next.scalar_one_or_none()
        if next_day:
            plan_id = next_day.plan_id

    # If no plan exists for this week (or upcoming week on weekends), plan is None (resets!)
    plan = await _get_plan(db, plan_id) if plan_id else None

    # Filter out Saturday and Sunday from the returned plan days
    if plan and plan.days:
        plan.days = [
            d for d in plan.days
            if datetime.fromisoformat(d.date).weekday() < 5
        ]

    return {
        "today": MealPlanDayOut.model_validate(today_day) if today_day else None,
        "plan": MealPlanOut.model_validate(plan) if plan else None,
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
