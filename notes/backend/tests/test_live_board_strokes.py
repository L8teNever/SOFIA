import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.auth import ACCESS_EMAIL_HEADER
from app.main import app


def run(coro):
    return asyncio.run(coro)


SIMON = {ACCESS_EMAIL_HEADER: "simon@example.com"}


class LiveBoardStrokeTests(unittest.TestCase):
    """Live-Striche auf einer Board-Seite: die anderen muessen boardPage mitbekommen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db.use_database(str(Path(self.tmp.name) / "t.db"))
        db._lock = asyncio.Lock()  # jeder Test hat seinen eigenen Event-Loop
        run(db.add_person_email("simon", "simon@example.com"))
        self.client = TestClient(app)
        self.board = run(db.create_board("simon", "Heft", None))

    def tearDown(self):
        self.tmp.cleanup()

    def next_of(self, ws, kind):
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("type") == kind:
                return msg
        self.fail("keine Nachricht " + kind)

    def test_start_and_end_carry_board_page(self):
        url = "/ws?board=" + self.board["id"]
        # ein gemeinsamer Event-Loop fuer beide Verbindungen (sonst haengt der zweite Handshake)
        with self.client as c, c.websocket_connect(url, headers=SIMON) as a, c.websocket_connect(url, headers=SIMON) as b:
            a.receive_json()  # init
            b.receive_json()  # init
            extra = {"boardPage": "page-1"}
            a.send_json({"type": "stroke_start", "strokeId": "s1", "tool": "pen", "color": "#000", "size": 3, "points": [{"x": 1, "y": 2}], "extra": extra})
            start = self.next_of(b, "stroke_start")
            self.assertEqual(start.get("extra"), extra)
            a.send_json({"type": "stroke_points", "strokeId": "s1", "points": [{"x": 3, "y": 4}]})
            a.send_json({"type": "stroke_end", "strokeId": "s1", "extra": extra})
            end = self.next_of(b, "stroke_end")
            self.assertEqual(end.get("extra"), extra)
        strokes = run(db.load_all(self.board["id"]))
        saved = [s for s in strokes if s["id"] == "s1"]
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0].get("extra") or {}).get("boardPage"), "page-1")


if __name__ == "__main__":
    unittest.main()
