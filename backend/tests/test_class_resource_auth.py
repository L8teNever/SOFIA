"""Classmates can edit homework and class calendar events; outsiders cannot.

Mealplan day-edit/delete stay admin-only (see test_mealplan_auth.py).
Run: python -m unittest backend.tests.test_class_resource_auth
"""
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

from backend.auth import get_current_user, require_admin
from backend.database import get_db
from backend.models.user import UserRole
from backend.routes import calendar as cal
from backend.routes import homework as hw
from backend.routes.calendar import create_event, delete_event, update_event
from backend.routes.homework import create_homework, delete_homework, update_homework


def _depends_on(fn, target) -> bool:
    for param in inspect.signature(fn).parameters.values():
        default = param.default
        if isinstance(default, DependsParam) and default.dependency is target:
            return True
    return False


class _User:
    def __init__(self, role="student", user_id=7, class_id=1):
        self.id = user_id
        self.email = f"u{user_id}@sofia.test"
        self.display_name = f"user{user_id}"
        self.role = UserRole(role)
        self.class_id = class_id


def _hw(class_id=1, created_by=1, hw_id=10):
    return SimpleNamespace(
        id=hw_id,
        subject_id=3,
        class_id=class_id,
        description="Alte Aufgabe",
        due_date="2026-10-10",
        created_by=created_by,
        created_at=None,
        checked_by=[],
        file_url=None,
        file_type=None,
        attachments=[],
    )


def _event(class_id=1, created_by=1, event_id=20, event_type="other"):
    return SimpleNamespace(
        id=event_id,
        title="Klassenfahrt",
        description=None,
        date="2026-11-01",
        end_date=None,
        time=None,
        event_type=event_type,
        class_id=class_id,
        subject_id=None,
        created_by=created_by,
        attachments=[],
    )


class DepTests(unittest.TestCase):
    def test_homework_mutations_are_any_authenticated_user(self):
        for fn in (create_homework, update_homework, delete_homework):
            self.assertTrue(_depends_on(fn, get_current_user), fn.__name__)
            self.assertFalse(_depends_on(fn, require_admin), fn.__name__)

    def test_calendar_mutations_are_any_authenticated_user(self):
        for fn in (create_event, update_event, delete_event):
            self.assertTrue(_depends_on(fn, get_current_user), fn.__name__)
            self.assertFalse(_depends_on(fn, require_admin), fn.__name__)

    def test_homework_update_delete_do_not_check_creator(self):
        src = inspect.getsource(update_homework) + inspect.getsource(delete_homework)
        self.assertNotIn("created_by != current_user.id", src)
        self.assertIn("hw.class_id != current_user.class_id", src)

    def test_calendar_update_delete_stay_class_scoped(self):
        src = inspect.getsource(update_event) + inspect.getsource(delete_event)
        self.assertIn("event.class_id != current_user.class_id", src)
        self.assertIn('event.event_type == "personal"', src)


class UiGateTests(unittest.TestCase):
    def test_homework_cards_show_edit_menu_without_creator_gate(self):
        with open("pages/homework.html", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("moreMenuBtn(`openHwCardMenu(this, ${h.id})`)", html)
        self.assertNotRegex(
            html,
            r"created_by[\s\S]{0,80}openHwCardMenu",
        )

    def test_calendar_class_events_editable_by_any_classmate(self):
        with open("pages/calendar.html", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("function calCanModifyEvent(ev)", html)
        self.assertIn("if (ev.event_type === 'personal') return ev.created_by === calCurrentUser.id;", html)
        self.assertIn("return true;", html)


async def _db_with(item):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = item
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.delete = AsyncMock()
    db.refresh = AsyncMock()
    yield db


def _client_for(module, user, item=None):
    app = FastAPI()
    app.include_router(module.router)

    async def override_db():
        async for db in _db_with(item):
            yield db

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


class HomeworkClassmateApiTests(unittest.TestCase):
    def test_classmate_can_update_other_students_homework(self):
        classmate = _User(user_id=2, class_id=1)
        item = _hw(class_id=1, created_by=1)
        client = _client_for(hw, classmate, item)
        with patch("backend.routes.homework.log_audit", new_callable=AsyncMock):
            res = client.put("/api/v1/homework/10", json={"description": "Neue Beschreibung"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(item.description, "Neue Beschreibung")

    def test_classmate_can_delete_other_students_homework(self):
        classmate = _User(user_id=2, class_id=1)
        item = _hw(class_id=1, created_by=1)
        client = _client_for(hw, classmate, item)
        with patch("backend.routes.homework.log_audit", new_callable=AsyncMock):
            res = client.delete("/api/v1/homework/10")
        self.assertEqual(res.status_code, 200, res.text)

    def test_outsider_cannot_update_or_delete_homework(self):
        outsider = _User(user_id=9, class_id=2)
        item = _hw(class_id=1, created_by=1)
        client = _client_for(hw, outsider, item)
        put_res = client.put("/api/v1/homework/10", json={"description": "Nope"})
        self.assertEqual(put_res.status_code, 403)
        del_res = client.delete("/api/v1/homework/10")
        self.assertEqual(del_res.status_code, 403)


class CalendarClassmateApiTests(unittest.TestCase):
    def test_classmate_can_update_class_event(self):
        classmate = _User(user_id=2, class_id=1)
        item = _event(class_id=1, created_by=1, event_type="other")
        client = _client_for(cal, classmate, item)
        with patch("backend.routes.calendar.log_audit", new_callable=AsyncMock):
            res = client.put("/api/v1/calendar/20", json={
                "title": "Umbenannt",
                "date": "2026-11-02",
                "event_type": "other",
            })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(item.title, "Umbenannt")

    def test_classmate_can_delete_class_event(self):
        classmate = _User(user_id=2, class_id=1)
        item = _event(class_id=1, created_by=1)
        client = _client_for(cal, classmate, item)
        with patch("backend.routes.calendar.log_audit", new_callable=AsyncMock):
            res = client.delete("/api/v1/calendar/20")
        self.assertEqual(res.status_code, 200, res.text)

    def test_outsider_cannot_update_or_delete_event(self):
        outsider = _User(user_id=9, class_id=2)
        item = _event(class_id=1, created_by=1)
        client = _client_for(cal, outsider, item)
        put_res = client.put("/api/v1/calendar/20", json={
            "title": "Nope",
            "date": "2026-11-02",
            "event_type": "other",
        })
        self.assertEqual(put_res.status_code, 403)
        del_res = client.delete("/api/v1/calendar/20")
        self.assertEqual(del_res.status_code, 403)

    def test_personal_event_stays_creator_only(self):
        classmate = _User(user_id=2, class_id=1)
        item = _event(class_id=1, created_by=1, event_type="personal")
        client = _client_for(cal, classmate, item)
        put_res = client.put("/api/v1/calendar/20", json={
            "title": "Nope",
            "date": "2026-11-02",
            "event_type": "personal",
        })
        self.assertEqual(put_res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
