"""Retry/backoff for Gemini 429/503. Run: python -m unittest backend.tests.test_gemini_retry"""
import asyncio
import unittest
from unittest.mock import patch

from backend.gemini_client import GeminiError, _call_gemini_json


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text or ""

    def json(self):
        return self._payload


_OK = _Resp(200, {
    "candidates": [{
        "finishReason": "STOP",
        "content": {"parts": [{"text": '[{"date":"2026-10-06","main":"Pasta"}]'}]},
    }],
})
_BUSY = _Resp(503, {"error": {"message": "high demand"}}, "high demand")


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, params=None, json=None):
        self.urls.append(url)
        if not self._responses:
            raise AssertionError("unexpected extra Gemini call")
        return self._responses.pop(0)


class GeminiRetryTests(unittest.TestCase):
    def test_503_then_success_saves_without_raising(self):
        client = _Client([_BUSY, _OK])

        async def run():
            with patch("backend.gemini_client.httpx.AsyncClient", return_value=client), \
                 patch("backend.gemini_client.settings") as settings, \
                 patch("backend.gemini_client.asyncio.sleep", return_value=None):
                settings.gemini_api_key = "test-key"
                settings.gemini_model = "gemini-3.6-flash"
                settings.gemini_fallback_model = ""
                return await _call_gemini_json("prompt")

        parsed = asyncio.run(run())
        self.assertEqual(parsed[0]["main"], "Pasta")
        self.assertEqual(len(client.urls), 2)

    def test_exhausted_503_then_fallback_model(self):
        busy = [_BUSY, _BUSY, _BUSY, _BUSY]
        client = _Client(busy + [_OK])

        async def run():
            with patch("backend.gemini_client.httpx.AsyncClient", return_value=client), \
                 patch("backend.gemini_client.settings") as settings, \
                 patch("backend.gemini_client.asyncio.sleep", return_value=None):
                settings.gemini_api_key = "test-key"
                settings.gemini_model = "gemini-3.6-flash"
                settings.gemini_fallback_model = "gemini-2.5-flash"
                return await _call_gemini_json("prompt")

        parsed = asyncio.run(run())
        self.assertEqual(parsed[0]["date"], "2026-10-06")
        self.assertEqual(len(client.urls), 5)
        self.assertTrue(any("gemini-2.5-flash" in u for u in client.urls))

    def test_400_does_not_retry(self):
        client = _Client([_Resp(400, {"error": {"message": "bad request"}})])

        async def run():
            with patch("backend.gemini_client.httpx.AsyncClient", return_value=client), \
                 patch("backend.gemini_client.settings") as settings, \
                 patch("backend.gemini_client.asyncio.sleep", return_value=None) as sleep:
                settings.gemini_api_key = "test-key"
                settings.gemini_model = "gemini-3.6-flash"
                settings.gemini_fallback_model = ""
                with self.assertRaises(GeminiError):
                    await _call_gemini_json("prompt")
                sleep.assert_not_called()

        asyncio.run(run())
        self.assertEqual(len(client.urls), 1)


class MealplanStatusPersistTests(unittest.TestCase):
    def test_error_status_roundtrip(self):
        import os, tempfile
        from backend.routes import mealplan as mp

        with tempfile.TemporaryDirectory() as tmp:
            old_file = mp._STATUS_FILE
            old_status = dict(mp.upload_status)
            try:
                mp._STATUS_FILE = os.path.join(tmp, "_upload_status.json")
                mp.upload_status.clear()
                mp.upload_status.update(mp._default_status())
                mp.upload_status["status"] = "error"
                mp.upload_status["error"] = "Gemini-Anfrage fehlgeschlagen (503)"
                mp.upload_status["message"] = "Fehler bei KI-Erkennung"
                mp.upload_status["filename"] = "abc.jpg"
                mp._save_status()
                loaded = mp._load_status()
                self.assertEqual(loaded["status"], "error")
                self.assertIn("503", loaded["error"])
                self.assertEqual(loaded["filename"], "abc.jpg")
            finally:
                mp._STATUS_FILE = old_file
                mp.upload_status.clear()
                mp.upload_status.update(old_status)


if __name__ == "__main__":
    unittest.main()
