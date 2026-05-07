"""
Run with: python -m backend.seed
Creates demo data for development.
"""
import asyncio
from backend.database import init_db, AsyncSessionLocal
from backend.models.user import User, UserRole
from backend.models.class_group import ClassGroup
from backend.models.subject import Subject
from backend.models.homework import Homework
from backend.models.calendar_event import CalendarEvent
from backend.config import settings

async def seed():
    await init_db()
    async with AsyncSessionLocal() as db:
        # Create class
        cls = ClassGroup(name="10a")
        db.add(cls)
        await db.flush()

        # Create subjects
        subjects = [
            Subject(name="Mathematik", short_name="M", color="#d3e3fd", class_id=cls.id),
            Subject(name="Deutsch", short_name="D", color="#eaddff", class_id=cls.id),
            Subject(name="Englisch", short_name="E", color="#c4eed0", class_id=cls.id),
            Subject(name="Physik", short_name="Ph", color="#ffdec1", class_id=cls.id),
            Subject(name="Sport", short_name="Sp", color="#ffd8e4", is_global=True),
        ]
        for s in subjects: db.add(s)
        await db.flush()

        # Create demo user if DEV_EMAIL is set
        if settings.dev_email:
            user = User(
                email=settings.dev_email,
                display_name="Demo User",
                role=UserRole.super_admin,
                class_id=cls.id,
            )
            db.add(user)
            await db.flush()

            # Sample homework
            from datetime import date, timedelta
            today = date.today()
            hw = Homework(
                subject_id=subjects[0].id, class_id=cls.id,
                description="Seite 42, Aufgaben 1-5", due_date=(today+timedelta(days=2)).isoformat(),
                created_by=user.id, checked_by=[],
            )
            db.add(hw)

            # Sample calendar event
            ev = CalendarEvent(
                title="Mathearbeit", date=(today+timedelta(days=7)).isoformat(),
                event_type="exam", class_id=cls.id, created_by=user.id,
            )
            db.add(ev)

        await db.commit()
        print("Seed complete!")

asyncio.run(seed())
