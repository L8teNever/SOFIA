import os
import shutil
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, desc
from backend.database import get_db
from backend.auth import require_super_admin, require_admin, get_current_user
from backend.models.user import User
from backend.models.class_group import ClassGroup
from backend.models.subject import Subject
from backend.models.audit_log import AuditLog
from backend.schemas import UserOut, ClassGroupOut, StorageStatsOut, StorageCategoryStats, AuditLogOut
from backend.config import settings
from backend.routes.mealplan import cleanup_past_mealplans
from backend.services.audit_service import log_audit
from typing import List, Optional

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

def _dir_stats(path: str) -> tuple[int, int]:
    """Returns (total_bytes, file_count) in directory."""
    if not os.path.exists(path):
        return 0, 0
    total_bytes = 0
    count = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_bytes += os.path.getsize(fp)
                count += 1
            except OSError:
                pass
    return total_bytes, count

@router.get("/stats")
async def admin_stats(db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    users = (await db.execute(select(User))).scalars().all()
    classes = (await db.execute(select(ClassGroup))).scalars().all()
    return {"users": len(users), "classes": len(classes)}

@router.get("/users", response_model=List[UserOut])
async def all_users(db: AsyncSession = Depends(get_db), _: User = Depends(require_super_admin)):
    result = await db.execute(select(User))
    return result.scalars().all()

@router.get("/class-users", response_model=List[UserOut])
async def class_users(db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    result = await db.execute(select(User).where(User.class_id == current_user.class_id))
    return result.scalars().all()

# --- Speicher-Statistiken ---

@router.get("/storage-stats", response_model=StorageStatsOut)
async def storage_stats(_: User = Depends(require_super_admin)):
    up_dir = settings.upload_dir
    hw_bytes, hw_files = _dir_stats(os.path.join(up_dir, "homework"))
    mp_bytes, mp_files = _dir_stats(os.path.join(up_dir, "mealplan"))
    av_bytes, av_files = _dir_stats(os.path.join(up_dir, "avatars"))

    # Shared files (direct children in upload_dir)
    sh_bytes, sh_files = 0, 0
    if os.path.exists(up_dir):
        for item in os.listdir(up_dir):
            p = os.path.join(up_dir, item)
            if os.path.isfile(p):
                try:
                    sh_bytes += os.path.getsize(p)
                    sh_files += 1
                except OSError:
                    pass

    # Database file size
    db_bytes = 0
    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    if os.path.exists(db_path):
        try:
            db_bytes = os.path.getsize(db_path)
        except OSError:
            pass

    # Disk usage
    disk_total, disk_used, disk_free = shutil.disk_usage(up_dir if os.path.exists(up_dir) else ".")

    total_bytes = hw_bytes + mp_bytes + av_bytes + sh_bytes
    total_files = hw_files + mp_files + av_files + sh_files

    return StorageStatsOut(
        total_bytes=total_bytes,
        total_files=total_files,
        db_bytes=db_bytes,
        categories={
            "homework": StorageCategoryStats(bytes=hw_bytes, files=hw_files),
            "mealplan": StorageCategoryStats(bytes=mp_bytes, files=mp_files),
            "avatars": StorageCategoryStats(bytes=av_bytes, files=av_files),
            "files": StorageCategoryStats(bytes=sh_bytes, files=sh_files),
        },
        disk_total_bytes=disk_total,
        disk_free_bytes=disk_free,
    )

@router.post("/cleanup-mealplans")
async def admin_cleanup_mealplans(request: Request, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_super_admin)):
    result = await cleanup_past_mealplans(db)

    await log_audit(
        db,
        action="admin.cleanup_mealplans",
        user=current_user,
        entity_type="meal_plan",
        details=result,
        request=request,
    )

    return result

# --- Super-Admin Audit-Logs ---

@router.get("/audit-logs", response_model=List[AuditLogOut])
async def list_audit_logs(
    action: Optional[str] = None,
    user_id: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin)
):
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if search:
        search_fmt = f"%{search.strip()}%"
        query = query.where(
            or_(
                AuditLog.action.ilike(search_fmt),
                AuditLog.username.ilike(search_fmt),
                AuditLog.details.ilike(search_fmt),
                AuditLog.ip_address.ilike(search_fmt),
            )
        )

    query = query.order_by(desc(AuditLog.created_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    return result.scalars().all()
