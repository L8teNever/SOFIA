from app import db, goodnotes_export
from app.sofia_sync import _region_pdfs


def test_clean_hw_regions_keeps_valid_entries():
    out = db.clean_hw_regions(
        {
            "assigned": [5, "7", "x", 5, -1],
            "regions": {
                "5": [{"kind": "page", "pageId": "p1", "label": "Seite 1"}, {"kind": "area", "rect": [0, 0, 100, 50]}, {"kind": "bad"}],
                "abc": [{"kind": "page", "pageId": "p2"}],
            },
            "solutions": {"5": 999},
        },
        {"solutions": {"5": 12}},
    )
    assert out["assigned"] == [5, 7]
    assert [r["kind"] for r in out["regions"]["5"]] == ["page", "area"]
    assert "abc" not in out["regions"]
    assert out["solutions"] == {"5": 12}  # Loesungs-IDs bestimmt nur der Server


def test_region_pdfs_pages_and_areas():
    nb = {"layout": "vertical", "pages": [{"id": "p1", "paper": "blank"}, {"id": "p2", "paper": "blank"}]}
    board = {"notebook": nb, "title": "T"}
    strokes = [{"id": "s1", "tool": "pen", "color": "#000", "width": 3, "points": [{"x": 50, "y": 50}, {"x": 120, "y": 90}]}]
    files = _region_pdfs(board, strokes, [{"kind": "page", "pageId": "p2"}, {"kind": "area", "rect": [0, 0, 300, 200]}], "Blatt")
    assert [f[0] for f in files] == ["Blatt – Seiten.pdf", "Blatt – Ausschnitt 1.pdf"]
    assert all(f[1].startswith(b"%PDF") for f in files)
    assert goodnotes_export  # Modul geladen


def test_new_page_after_marked_page_joins_homework(monkeypatch):
    import asyncio
    from app import main

    saved = {}

    async def fake_set(board_id, data):
        saved["data"] = data

    async def fake_broadcast(*a, **k):
        return None

    monkeypatch.setattr(main.db, "set_hw_regions", fake_set)
    monkeypatch.setattr(main.manager, "broadcast", fake_broadcast)
    before = {
        "notebook": {"pages": [{"id": "a"}, {"id": "b"}]},
        "hwRegions": {"assigned": [7], "regions": {"7": [{"kind": "page", "pageId": "a"}]}, "solutions": {}},
    }
    nb = {"pages": [{"id": "a"}, {"id": "new"}, {"id": "b"}, {"id": "c"}]}
    asyncio.run(main._extend_hw_regions("x", before, nb))
    ids = [r["pageId"] for r in saved["data"]["regions"]["7"]]
    assert ids == ["a", "new"]  # nur die Seite direkt hinter der markierten
