from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_admin
from backend.models.user import User
from backend.models.notification import Notification
from backend.schemas import PushSubscriptionIn, PushNotificationIn, NotificationOut
from backend.config import settings
from typing import List
import json, asyncio, logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/push", tags=["push"])

@router.get("/vapid-public-key")
async def get_vapid_key():
    return {"public_key": settings.vapid_public_key}

@router.post("/subscribe")
async def subscribe(data: PushSubscriptionIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    current_user.push_subscription = json.dumps(data.subscription)
    await db.commit()
    return {"ok": True}

@router.post("/unsubscribe")
async def unsubscribe(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    current_user.push_subscription = None
    await db.commit()
    return {"ok": True}

@router.post("/send")
async def send_notification(data: PushNotificationIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    if not settings.vapid_private_key:
        raise HTTPException(503, "VAPID not configured")
    from pywebpush import webpush, WebPushException

    query = select(User).where(User.push_subscription.isnot(None))
    if data.target == "class":
        query = query.where(User.class_id == current_user.class_id)
    elif data.target.startswith("user:"):
        uid = int(data.target.split(":")[1])
        query = query.where(User.id == uid)

    result = await db.execute(query)
    users = list(result.scalars().all())

    # Admin always receives their own notification
    if current_user.push_subscription and not any(u.id == current_user.id for u in users):
        users.append(current_user)

    sent = 0
    expired_ids = []

    for u in users:
        try:
            sub = json.loads(u.push_subscription)
            await asyncio.to_thread(
                webpush,
                subscription_info=sub,
                data=json.dumps({"title": data.title, "body": data.body}),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_claim_email},
            )
            sent += 1
        except WebPushException as e:
            # 410 Gone = subscription expired/unregistered → clean up
            if e.response is not None and e.response.status_code in (404, 410):
                expired_ids.append(u.id)
            else:
                logger.warning("Push failed for user %s: %s", u.id, e)
        except Exception as e:
            logger.warning("Push error for user %s: %s", u.id, e)

    if expired_ids:
        expired_result = await db.execute(select(User).where(User.id.in_(expired_ids)))
        for u in expired_result.scalars().all():
            u.push_subscription = None

    # Persist notification in DB for every targeted user so they can review it later
    for u in users:
        db.add(Notification(user_id=u.id, title=data.title, body=data.body))
    await db.commit()

    return {"sent": sent}


@router.get("/notifications", response_model=List[NotificationOut])
async def list_notifications(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


@router.get("/notifications/unread-count")
async def unread_count(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False
        )
    )
    return {"count": result.scalar()}


@router.post("/notifications/{notif_id}/read")
async def mark_read(notif_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Notification).where(Notification.id == notif_id, Notification.user_id == current_user.id)
    )
    n = result.scalar_one_or_none()
    if n:
        n.is_read = True
        await db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_read(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    from sqlalchemy import update
    await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"ok": True}
