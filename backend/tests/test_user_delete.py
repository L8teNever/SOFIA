"""Super-Admin loescht/sperrt Nutzer: persoenliche Daten weg, Klasseninhalte bleiben.

Laeuft gegen eine echte SQLite-Datenbank im Speicher.
Run: python -m unittest backend.tests.test_user_delete
"""
import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.database import Base, get_db
from backend.auth import get_current_user
from backend.tests.test_calendar_categories import *  # noqa: F401,F403  (laedt alle Modelle)
from backend.models.class_group import ClassGroup
from backend.models.user import User, UserRole
from backend.models.grade import Grade
from backend.models.homework import Homework
from backend.models.subject import Subject
from backend.models.calendar_event import CalendarEvent
from backend.models.calendar_category import CalendarCategory
from backend.routes import users as users_route


class UserDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.Session() as db:
            db.add(ClassGroup(id=1, name="10a"))
            await db.flush()
            db.add_all([
                User(id=1, email="admin@x", role=UserRole.super_admin, class_id=1),
                User(id=2, email="weg@x", role=UserRole.student, class_id=1),
                User(id=3, email="bleibt@x", role=UserRole.student, class_id=1),
            ])
            db.add(Subject(id=1, name="Mathe", class_id=1))
            await db.flush()
            db.add(Grade(user_id=2, subject_id=1, value=2.0))
            db.add(Homework(id=10, subject_id=1, class_id=1, description="S. 5", due_date="2026-11-01", created_by=2, checked_by=[2, 3]))
            db.add(CalendarEvent(id=20, title="privat", date="2026-11-02", event_type="personal", class_id=1, created_by=2))
            db.add(CalendarEvent(id=21, title="Ausflug", date="2026-11-03", event_type="trip", class_id=1, created_by=2))
            db.add(CalendarCategory(id=5, name="Internat", member_ids=[2, 3]))
            await db.commit()

        async def _db():
            async with self.Session() as s:
                yield s

        self.as_user = 1

        async def _user():
            async with self.Session() as s:
                return await s.get(User, self.as_user)

        app = FastAPI()
        app.include_router(users_route.router)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _user
        users_route.log_audit = _noop_audit
        self.client = TestClient(app)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_delete_removes_personal_data_keeps_class_content(self):
        r = self.client.delete("/api/v1/users/2")
        self.assertEqual(r.status_code, 200, r.text)
        async with self.Session() as db:
            self.assertIsNone(await db.get(User, 2))
            self.assertEqual((await db.execute(select(Grade).where(Grade.user_id == 2))).scalars().all(), [])
            self.assertIsNone(await db.get(CalendarEvent, 20))           # persoenlich: weg
            self.assertIsNotNone(await db.get(CalendarEvent, 21))        # Klassentermin: bleibt
            hw = await db.get(Homework, 10)
            self.assertIsNotNone(hw)                                     # Hausaufgabe bleibt
            self.assertEqual(hw.checked_by, [3])                         # Haken ausgetragen
            self.assertEqual((await db.get(CalendarCategory, 5)).member_ids, [3])

    def test_cannot_delete_self_or_super_admin(self):
        self.assertEqual(self.client.delete("/api/v1/users/1").status_code, 400)

    def test_only_super_admin_may_delete(self):
        self.as_user = 3
        self.assertEqual(self.client.delete("/api/v1/users/2").status_code, 403)

    def test_block_and_remove_class(self):
        r = self.client.patch("/api/v1/users/2", json={"is_active": False, "class_id": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["is_active"], False)
        self.assertIsNone(r.json()["class_id"])
        self.assertEqual(self.client.patch("/api/v1/users/1", json={"is_active": False}).status_code, 400)


async def _noop_audit(*a, **k):
    return None


class BlockedLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_user_gets_403(self):
        from backend import auth
        blocked = User(id=9, email="b@x", role=UserRole.student, is_active=False)

        async def fake_resolve(email, db):
            return blocked
        orig = auth._resolve_user_by_email
        auth._resolve_user_by_email = fake_resolve
        try:
            class Req:
                headers = {"Cf-Access-Authenticated-User-Email": "b@x"}
            with self.assertRaises(HTTPException) as cm:
                await auth.get_current_user(Req(), None)
            self.assertEqual(cm.exception.status_code, 403)
        finally:
            auth._resolve_user_by_email = orig


if __name__ == "__main__":
    unittest.main()
