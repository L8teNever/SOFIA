import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.auth import ACCESS_EMAIL_HEADER
from app.main import app, manager


def run(coro):
    return asyncio.run(coro)


SIMON = {ACCESS_EMAIL_HEADER: "simon@example.com"}
FRANZ = {ACCESS_EMAIL_HEADER: "franz@example.com"}


class SecondDeviceTests(unittest.TestCase):
    """Zweites Geraet: finden und dort ein Blatt gross oeffnen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db.use_database(str(Path(self.tmp.name) / "t.db"))
        db._lock = asyncio.Lock()  # jeder Test hat seinen eigenen Event-Loop
        manager._clients.clear()  # keine liegengebliebenen Verbindungen aus anderen Tests
        run(db.add_person_email("simon", "simon@example.com"))
        run(db.add_person_email("franz", "franz@example.com"))
        self.client = TestClient(app)
        self.heft = run(db.create_board("simon", "Heft", None))
        self.aufgabe = run(db.create_board("simon", "Aufgabe", None))
        self.franz_board = run(db.create_board("franz", "Franz privat", None))

    def tearDown(self):
        self.tmp.cleanup()

    def next_of(self, ws, kind):
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("type") == kind:
                return msg
        self.fail("keine Nachricht " + kind)

    def test_own_device_elsewhere_gets_board(self):
        with self.client as c, c.websocket_connect("/ws?dev=iPad&board=" + self.heft["id"], headers=SIMON) as a, c.websocket_connect(
            "/ws?dev=iPad&board=" + self.aufgabe["id"], headers=SIMON
        ) as b:
            a.receive_json()
            b_init = b.receive_json()
            a.send_json({"type": "devices"})
            devs = self.next_of(a, "devices")["list"]
            self.assertEqual(len(devs), 1)
            self.assertEqual(devs[0]["id"], b_init["clientId"])
            self.assertTrue(devs[0]["mine"])
            self.assertFalse(devs[0]["here"])
            self.assertEqual(devs[0]["device"], "iPad")
            a.send_json({"type": "device_open", "target": b_init["clientId"], "board": self.aufgabe["id"], "title": "Aufgabe"})
            got = self.next_of(b, "device_open")
            self.assertEqual(got["board"], self.aufgabe["id"])
            self.assertEqual(got["person"], "simon")
            self.next_of(a, "device_open_sent")

    def test_other_person_only_in_same_board_and_needs_access(self):
        with self.client as c, c.websocket_connect("/ws?board=" + self.heft["id"], headers=SIMON) as a, c.websocket_connect(
            "/ws?board=" + self.franz_board["id"], headers=FRANZ
        ) as f:
            a.receive_json()
            f_init = f.receive_json()
            a.send_json({"type": "devices"})
            self.assertEqual(self.next_of(a, "devices")["list"], [])  # Franz sitzt an einem anderen Blatt
            # direkt anschicken geht auch nicht
            a.send_json({"type": "device_open", "target": f_init["clientId"], "board": self.heft["id"]})
            self.assertEqual(self.next_of(a, "device_open_failed")["reason"], "gone")

    def test_show_file_on_own_device_only_safe_urls(self):
        with self.client as c, c.websocket_connect("/ws?board=" + self.heft["id"], headers=SIMON) as a, c.websocket_connect(
            "/ws?board=" + self.aufgabe["id"], headers=SIMON
        ) as b:
            a.receive_json()
            b_init = b.receive_json()
            url = "/api/sofia/file?u=%2Fuploads%2Floesung.pdf"
            a.send_json({"type": "device_show", "target": b_init["clientId"], "url": url, "kind": "pdf", "name": "Loesung von Franz"})
            got = self.next_of(b, "device_show")
            self.assertEqual(got["items"], [{"url": url, "kind": "pdf", "name": "Loesung von Franz"}])
            self.assertEqual(got["person"], "simon")
            self.next_of(a, "device_open_sent")
            # ganze Aufgabe: Text + Dateien, fremde Adressen fliegen raus
            a.send_json({
                "type": "device_show", "target": b_init["clientId"], "title": "Mathe – Aufgabe", "text": "S. 12 Nr. 3",
                "items": [{"url": "/api/media/abc", "kind": "image"}, {"url": "https://evil.example/x", "kind": "image"}, {"url": url, "kind": "pdf"}],
            })
            got = self.next_of(b, "device_show")
            self.assertEqual(got["title"], "Mathe – Aufgabe")
            self.assertEqual(got["text"], "S. 12 Nr. 3")
            self.assertEqual([i["url"] for i in got["items"]], ["/api/media/abc", url])
            self.next_of(a, "device_open_sent")
            a.send_json({"type": "device_show", "target": b_init["clientId"], "url": "https://evil.example/x", "kind": "image"})
            self.assertEqual(self.next_of(a, "device_open_failed")["reason"], "url")


class SolutionsListTests(unittest.TestCase):
    def test_pack_solutions(self):
        from unittest import mock

        from app import sofia_sync

        raw = [
            {"id": 3, "text": "", "created_at": "2026-10-21T10:00:00", "user": {"display_name": "Franz", "email": "franz@x.de"},
             "attachments": [{"url": "/uploads/a.pdf", "type": "file", "name": "a.pdf"}, {"url": ""}]},
            {"id": 2, "text": "siehe Heft", "user": {"display_name": None, "email": "Simon@x.de"}, "attachments": []},
        ]

        async def ctx(_pid):
            return {"email": "simon@x.de"}, None

        with mock.patch.object(sofia_sync, "_homework_ctx", ctx), mock.patch.object(sofia_sync, "_get", lambda path, who: raw):
            out = run(sofia_sync.homework_solutions("simon", 7))
        self.assertEqual(out[0]["name"], "Franz")
        self.assertFalse(out[0]["mine"])
        self.assertEqual(out[0]["attachments"], [{"url": "/uploads/a.pdf", "type": "file", "name": "a.pdf"}])
        self.assertTrue(out[1]["mine"])
        self.assertEqual(out[1]["name"], "Simon")
        self.assertEqual(out[1]["text"], "siehe Heft")


if __name__ == "__main__":
    unittest.main()
