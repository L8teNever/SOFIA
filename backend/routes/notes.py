from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, delete
from backend.database import get_db, AsyncSessionLocal
from backend.auth import get_current_user, get_current_user_ws
from backend.models.notes_board import NotesBoard
from backend.models.notes_stroke import NotesStroke
from backend.models.drive_topic import DriveTopic
from backend.models.subject import Subject
from backend.models.user import User
from backend.schemas import NotesBoardOut, NotesBoardCreate, NotesBoardUpdate
from backend.services.notes_connection_manager import NotesConnectionManager
from typing import List, Optional
import json, os, uuid, re, base64

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])
manager = NotesConnectionManager()

_NOTES_MEDIA_DIR = os.path.join("uploads", "notes-media")
_NOTES_MEDIA_MAX = 3_500_000
_JPEG_DATA_URI = re.compile(r"^data:image/(jpeg|jpg);base64,(.+)$", re.I | re.S)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _pack_points(points, extra=None):
    if extra:
        return json.dumps({"pts": points, "extra": extra})
    return json.dumps(points)


def _unpack_points(raw):
    data = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(data, dict) and "pts" in data:
        return data.get("pts") or [], data.get("extra")
    if isinstance(data, list):
        return data, None
    return [], None


def _stroke_payload(row: NotesStroke) -> dict:
    pts, extra = _unpack_points(row.points)
    out = {"id": row.id, "tool": row.tool, "color": row.color, "size": row.size, "points": pts}
    if extra:
        out["extra"] = extra
    return out
