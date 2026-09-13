from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.grade import Grade
from backend.models.subject_weighting import SubjectWeighting
from backend.models.user import User
from backend.schemas import GradeOut, GradeCreate, SubjectWeightingOut, SubjectWeightingCreate
from backend.services.audit_service import log_audit
from datetime import datetime, timezone
from typing import List

router = APIRouter(prefix="/api/v1/grades", tags=["grades"])

@router.get("/", response_model=List[GradeOut])
async def list_grades(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Grade).where(Grade.user_id == current_user.id).order_by(Grade.date.desc()))
    return result.scalars().all()

@router.post("/", response_model=GradeOut)
async def create_grade(request: Request, data: GradeCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    grade = Grade(user_id=current_user.id, **data.model_dump())
    db.add(grade)
    await db.commit()
    await db.refresh(grade)

    await log_audit(
        db,
        action="grade.create",
        user=current_user,
        entity_type="grade",
        entity_id=grade.id,
        details={"subject_id": grade.subject_id, "value": grade.value, "label": grade.label, "weight_type": grade.weight_type},
        request=request,
    )

    return grade

@router.delete("/{grade_id}")
async def delete_grade(request: Request, grade_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Grade).where(Grade.id == grade_id, Grade.user_id == current_user.id))
    grade = result.scalar_one_or_none()
    if not grade:
        raise HTTPException(404)
    val = grade.value
    sub_id = grade.subject_id
    await db.delete(grade)
    await db.commit()

    await log_audit(
        db,
        action="grade.delete",
        user=current_user,
        entity_type="grade",
        entity_id=grade_id,
        details={"subject_id": sub_id, "value": val},
        request=request,
    )

    return {"ok": True}

# --- Notengewichtung pro Fach für die Klasse (Subject Weightings) ---

@router.get("/weightings", response_model=List[SubjectWeightingOut])
async def list_weightings(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        return []
    result = await db.execute(
        select(SubjectWeighting).where(SubjectWeighting.class_id == current_user.class_id)
    )
    return result.scalars().all()

@router.post("/weightings", response_model=SubjectWeightingOut)
async def set_weighting(request: Request, data: SubjectWeightingCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        raise HTTPException(400, "Du bist keiner Klasse zugeordnet")

    result = await db.execute(
        select(SubjectWeighting).where(
            SubjectWeighting.class_id == current_user.class_id,
            SubjectWeighting.subject_id == data.subject_id
        )
    )
    sw = result.scalar_one_or_none()

    if sw:
        sw.exam_weight = data.exam_weight
        sw.oral_weight = data.oral_weight
        sw.planned_exams = data.planned_exams
        sw.updated_by = current_user.id
        sw.updated_at = datetime.now(timezone.utc)
    else:
        sw = SubjectWeighting(
            class_id=current_user.class_id,
            subject_id=data.subject_id,
            exam_weight=data.exam_weight,
            oral_weight=data.oral_weight,
            planned_exams=data.planned_exams,
            updated_by=current_user.id,
        )
        db.add(sw)

    await db.commit()
    await db.refresh(sw)

    await log_audit(
        db,
        action="grades.weighting_update",
        user=current_user,
        entity_type="subject_weighting",
        entity_id=sw.id,
        details={
            "subject_id": data.subject_id,
            "class_id": current_user.class_id,
            "exam_weight": data.exam_weight,
            "oral_weight": data.oral_weight,
            "planned_exams": data.planned_exams
        },
        request=request,
    )

    return sw
