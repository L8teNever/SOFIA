"""Eigene Kalender-Kategorien: Termine sehen nur die Mitglieder, egal welche Klasse.

Laeuft gegen eine echte SQLite-Datenbank im Speicher.
Run: python -m unittest backend.tests.test_calendar_categories
"""
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.database import Base
from backend.models import (  # noqa: F401  alle Modelle laden, wie init_db
    user, class_group, subject, calendar_event, homework, homework_solution,
    grade, shared_file, notification, meal_plan,
    push_subscription, notification_setting, sent_notification_log,
    subject_weighting, audit_log, manual_timetable_entry,
    google_account, google_sync_map, user_email_alias,
    chat_conversation, chat_participant, chat_message, chat_poll_option, chat_poll_vote,
    chat_message_reaction, calendar_category,
)
from backend.models.calendar_category import CalendarCategory
from backend.models.calendar_event import CalendarEvent
from backend.models.class_group import ClassGroup
from backend.models.user import User, UserRole
from backend.routes import calendar as cal


class CategoryVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.Session() as db:
            db.add_all([ClassGroup(id=1, name="10a"), ClassGroup(id=2, name="10b")])
            await db.flush()
            # 1 und 3 sind im Internat (verschiedene Klassen), 2 nicht
            db.add_all([
                User(id=1, email="a@x", role=UserRole.student, class_id=1),
                User(id=2, email="b@x", role=UserRole.student, class_id=1),
                User(id=3, email="c@x", role=UserRole.student, class_id=2),
            ])
            await db.flush()
            db.add(CalendarCategory(id=5, name="Internat", color="#123456", member_ids=[1, 3]))
            db.add(CalendarEvent(id=50, title="Internatsabend", date="2026-11-02", event_type="custom",
                                 category_id=5, class_id=1, created_by=1))
            db.add(CalendarEvent(id=51, title="Klassenfahrt", date="2026-11-03", event_type="trip",
                                 class_id=1, created_by=2))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _titles(self, uid):
        async with self.Session() as db:
            u = await db.get(User, uid)
            return sorted(e.title for e in await cal.list_events(None, db, u))

    async def test_member_sees_category_event_and_own_class(self):
        self.assertEqual(await self._titles(1), ["Internatsabend", "Klassenfahrt"])

    async def test_classmate_without_membership_does_not_see_it(self):
        self.assertEqual(await self._titles(2), ["Klassenfahrt"])

    async def test_member_in_other_class_sees_only_category_event(self):
        self.assertEqual(await self._titles(3), ["Internatsabend"])

    async def test_non_member_cannot_edit_or_use_category(self):
        async with self.Session() as db:
            ev = await db.get(CalendarEvent, 50)
            u1, u2, u3 = [await db.get(User, i) for i in (1, 2, 3)]
            with self.assertRaises(HTTPException):
                await cal._check_event_access(db, ev, u2)
            await cal._check_event_access(db, ev, u3)  # Mitglied aus anderer Klasse: ok
            with self.assertRaises(HTTPException):
                await cal._resolve_type(db, SimpleNamespace(event_type="custom", category_id=5), u2)
            self.assertEqual(await cal._resolve_type(db, SimpleNamespace(event_type="custom", category_id=5), u1), ("custom", 5))


if __name__ == "__main__":
    unittest.main()


class CalendarPageTests(unittest.TestCase):
    def test_fetch_helper_calls_api_not_itself(self):
        # Regression: calFetchEvents rief sich selbst auf -> Kalender fuer alle leer
        import re
        with open("pages/calendar.html", encoding="utf-8") as f:
            html = f.read()
        body = re.search(r"async function calFetchEvents\(month\) \{(.*?)\n\}", html, re.S).group(1)
        self.assertIn("API.events(month)", body)
        self.assertNotIn("calFetchEvents(", body)
