from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.meal_plan import MealPlan, MealPlanDay
from backend.models.user import User
from backend.schemas import MealPlanOut, MealPlanDayOut, MealPlanDayUpdate
from backend.config import settings
from backend.gemini_client import extract_meal_days, GeminiError
from datetime import date
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
        db.add(MealPlanDay(plan_id=plan.id, date=d["date"], meal=d["meal"], edited=False))
    await db.commit()
    return await _get_plan(db, plan.id)


@router.get("/current")
async def current_meal_plan(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    today = date.today().isoformat()

    # Newest plan whose table includes today wins on overlap (e.g. next
    # week's plan uploaded a few days early while this week's is still live).
    result = await db.execute(
        select(MealPlanDay)
        .join(MealPlan)
        .where(MealPlanDay.date == today)
        .order_by(MealPlan.created_at.desc())
        .limit(1)
    )
    today_day = result.scalar_one_or_none()

    plan_id = today_day.plan_id if today_day else None
    if plan_id is None:
        latest = await db.execute(select(MealPlan.id).order_by(MealPlan.created_at.desc()).limit(1))
        plan_id = latest.scalar_one_or_none()

    plan = await _get_plan(db, plan_id) if plan_id else None
    return {
        "today": MealPlanDayOut.model_validate(today_day) if today_day else None,
        "plan": MealPlanOut.model_validate(plan) if plan else None,
    }


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
