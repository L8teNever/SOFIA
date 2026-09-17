from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user, require_admin
from backend.models.user import User
from backend.models.notification import Notification
from backend.models.push_subscription import PushSubscription
from backend.schemas import PushSubscriptionIn, PushUnsubscribeIn, PushNotificationIn, NotificationOut
from backend.config import settings
from typing import List, Optional
import json, asyncio, logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/push", tags=["push"])

async def push_to_users(db: AsyncSession, users: List[User], title: str, body: str, tag: Optional[str] = None, url: Optional[str] = None) -> int:
    """Sends a web push to every subscribed device of `users`, persists a
    Notification row for each (even unsubscribed ones, so it shows up in
    their in-app notification list), and cleans up expired subscriptions.
    tag groups related notifications on the OS side — e.g. chat passes the
    conversation id so several messages from the same chat replace each
    other in the notification tray instead of piling up (sw.js pairs this
    with renotify:true). url is where tapping the notification navigates;
    defaults to '/' in the service worker when omitted."""
    if not users:
        return 0

    sent = 0
    user_ids = [u.id for u in users]

    if settings.vapid_private_key:
        from pywebpush import webpush, WebPushException

        # Fetch all device subscriptions for these users
        sub_rows_res = await db.execute(select(PushSubscription).where(PushSubscription.user_id.in_(user_ids)))
        sub_rows = list(sub_rows_res.scalars().all())

        # Collect targets: list of (subscription_dict, push_sub_id, user_id)
        targets = []
        covered_user_ids = set()
        for s in sub_rows:
            try:
                targets.append((json.loads(s.subscription_json), s.id, s.user_id))
                covered_user_ids.add(s.user_id)
            except Exception:
                pass

        # Also support legacy users.push_subscription if user has no PushSubscription row
        for u in users:
            if u.id not in covered_user_ids and u.push_subscription:
                try:
                    targets.append((json.loads(u.push_subscription), None, u.id))
                except Exception:
                    pass

        payload = {"title": title, "body": body}
        if tag:
            payload["tag"] = tag
        if url:
            payload["url"] = url

        async def _send_one(target):
            sub_dict, sub_id, uid = target
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info=sub_dict,
                    data=json.dumps(payload),
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_claim_email},
                )
                return "sent"
            except WebPushException as e:
                if e.response is not None and e.response.status_code in (404, 410):
                    return ("expired", sub_id, uid)
                logger.warning("Push failed for user %s: %s", uid, e)
                return None
            except Exception as e:
                logger.warning("Push error for user %s: %s", uid, e)
                return None

        if targets:
            results = await asyncio.gather(*(_send_one(t) for t in targets))
            sent = sum(1 for r in results if r == "sent")

            # Clean up expired endpoints
            expired = [r for r in results if isinstance(r, tuple) and r[0] == "expired"]
            for _, sub_id, uid in expired:
                if sub_id is not None:
                    del_sub = await db.execute(select(PushSubscription).where(PushSubscription.id == sub_id))
                    s_obj = del_sub.scalar_one_or_none()
                    if s_obj:
                        await db.delete(s_obj)
                else:
                    u_res = await db.execute(select(User).where(User.id == uid))
                    u_obj = u_res.scalar_one_or_none()
                    if u_obj:
                        u_obj.push_subscription = None

    for u in users:
        db.add(Notification(user_id=u.id, title=title, body=body))
    await db.commit()
    return sent

@router.get("/vapid-public-key")
async def get_vapid_key():
    return {"public_key": settings.vapid_public_key}

@router.post("/subscribe")
async def subscribe(data: PushSubscriptionIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    sub_data = data.subscription
    raw_sub = json.dumps(sub_data)
    endpoint = sub_data.get("endpoint") if isinstance(sub_data, dict) else None

    if endpoint:
        # Check if this endpoint already exists
        res = await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
        existing = res.scalar_one_or_none()
        if existing:
            existing.user_id = current_user.id
            existing.subscription_json = raw_sub
            if data.user_agent:
                existing.user_agent = data.user_agent
        else:
            new_sub = PushSubscription(
                user_id=current_user.id,
                endpoint=endpoint,
                subscription_json=raw_sub,
                user_agent=data.user_agent
            )
            db.add(new_sub)

    current_user.push_subscription = raw_sub
    await db.commit()
    return {"ok": True}

@router.post("/unsubscribe")
async def unsubscribe(data: Optional[PushUnsubscribeIn] = None, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    endpoint = data.endpoint if data else None
    if endpoint:
        res = await db.execute(
            select(PushSubscription).where(
                PushSubscription.user_id == current_user.id,
                PushSubscription.endpoint == endpoint
            )
        )
        existing = res.scalar_one_or_none()
        if existing:
            await db.delete(existing)
    else:
        # Unsubscribe all devices of this user
        res = await db.execute(select(PushSubscription).where(PushSubscription.user_id == current_user.id))
        for s in res.scalars().all():
            await db.delete(s)
        current_user.push_subscription = None

    await db.commit()
    return {"ok": True}


@router.post("/send")
async def send_notification(data: PushNotificationIn, db: AsyncSession = Depends(get_db), current_user: User = Depends(require_admin)):
    if not settings.vapid_private_key:
        raise HTTPException(503, "VAPID not configured")

    query = select(User)
    if data.target == "class":
        query = query.where(User.class_id == current_user.class_id)
    elif data.target.startswith("user:"):
        uid = int(data.target.split(":")[1])
        query = query.where(User.id == uid)
    else:
        query = query.where(User.push_subscription.isnot(None))

    result = await db.execute(query)
    users = list(result.scalars().all())

    # Admin always receives their own notification
    if not any(u.id == current_user.id for u in users):
        users.append(current_user)

    sent = await push_to_users(db, users, data.title, data.body)
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
