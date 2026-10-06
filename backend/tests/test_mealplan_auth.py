"""Mealplan upload/retry is open to any authenticated Sofia user (including students).

Run: python -m unittest backend.tests.test_mealplan_auth
"""
import inspect
import io
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

from backend.auth import get_current_user, require_admin
from backend.database import get_db
from backend.models.user import UserRole
from backend.routes import mealplan as mp
from backend.routes.mealplan import (
    delete_meal_plan,
    retry_meal_plan,
    update_meal_plan_day,
    upload_meal_plan,
)


def _depends_on(fn, target) -> bool:
    for param in inspect.signature(fn).parameters.values():
        default = param.default
        if isinstance(default, DependsParam) and default.dependency is target:
            return True
    return False


class _User:
    def __init__(self, role, user_id=7):
        self.id = user_id
        self.email = f"{role}@sofia.test"
        self.display_name = role
        self.role = UserRole(role)


class MealplanDepTests(unittest.TestCase):
    def test_upload_and_retry_use_any_authenticated_user(self):
        self.assertTrue(_depends_on(upload_meal_plan, get_current_user))
        self.assertTrue(_depends_on(retry_meal_plan, get_current_user))
        self.assertFalse(_depends_on(upload_meal_plan, require_admin))
        self.assertFalse(_depends_on(retry_meal_plan, require_admin))

    def test_delete_and_day_edit_stay_admin_only(self):
        self.assertTrue(_depends_on(delete_meal_plan, require_admin))
        self.assertTrue(_depends_on(update_meal_plan_day, require_admin))


class MealplanUiGateTests(unittest.TestCase):
    def test_init_shows_fab_for_any_logged_in_user(self):
        with open("pages/mealplan.html", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("mealCanUpload = !!me", html)
        self.assertIn("fab.style.display = mealCanUpload ? 'flex' : 'none'", html)
        self.assertNotRegex(
            html,
            r"meal-fab[\s\S]{0,400}role === 'admin'",
        )
        self.assertIn("id=\"meal-fab\"", html)
        self.assertIn("retry_available && mealCanUpload", html)


async def _fake_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.delete = AsyncMock()
    db.refresh = AsyncMock()
    yield db


def _client_for(user):
    app = FastAPI()
    app.include_router(mp.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


class MealplanStudentApiTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_status = dict(mp.upload_status)
        self._old_file = mp._STATUS_FILE
        self._old_upload_dir = mp.settings.upload_dir
        mp.settings.upload_dir = self._tmpdir.name
        mp._STATUS_FILE = os.path.join(self._tmpdir.name, "_upload_status.json")
        mp.upload_status.clear()
        mp.upload_status.update(mp._default_status())

    def tearDown(self):
        mp._STATUS_FILE = self._old_file
        mp.settings.upload_dir = self._old_upload_dir
        mp.upload_status.clear()
        mp.upload_status.update(self._old_status)
        self._tmpdir.cleanup()

    def test_student_can_upload(self):
        student = _User("student")
        client = _client_for(student)
        with patch("backend.routes.mealplan.scan_file", new_callable=AsyncMock), \
             patch("backend.routes.mealplan.compress_lossless", return_value=(b"img", ".jpg", "image/jpeg")), \
             patch("backend.routes.mealplan.log_audit", new_callable=AsyncMock), \
             patch("backend.routes.mealplan.asyncio.create_task"):
            res = client.post(
                "/api/v1/mealplan/upload",
                files={"file": ("plan.jpg", io.BytesIO(b"fake-image"), "image/jpeg")},
            )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("status"), "processing")

    def test_student_can_retry(self):
        student = _User("student")
        client = _client_for(student)
        plan_dir = os.path.join(self._tmpdir.name, "mealplan")
        os.makedirs(plan_dir, exist_ok=True)
        fname = "retry-me.jpg"
        with open(os.path.join(plan_dir, fname), "wb") as f:
            f.write(b"img")
        mp.upload_status.update({
            "status": "error",
            "filename": fname,
            "content_type": "image/jpeg",
            "user_id": student.id,
            "error": "503",
            "message": "fail",
        })
        with patch("backend.routes.mealplan.asyncio.create_task"):
            res = client.post("/api/v1/mealplan/retry")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json().get("status"), "processing")

    def test_student_cannot_delete_or_edit_day(self):
        student = _User("student")
        client = _client_for(student)
        del_res = client.delete("/api/v1/mealplan/1")
        self.assertEqual(del_res.status_code, 403)
        patch_res = client.patch("/api/v1/mealplan/day/1", json={"meal": "Pasta"})
        self.assertEqual(patch_res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
