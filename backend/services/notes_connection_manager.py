"""WebSocket connection + presence management for Notes (Tafel) boards.

Near-mechanical port of sofianotes' ws_manager.py, with one extra dict
level so broadcasts are scoped per-board instead of one single global
board. Same in-memory-only design (no cross-process fanout) — safe as
long as the app runs as a single uvicorn worker, which it does today
(see the Dockerfile's CMD)."""
import itertools
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket

# Distinct, readable colors used to identify each connected participant's
# live cursor/presence marker (independent of whatever pen color they draw
# with) — same palette sofianotes already used.
PRESENCE_PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45",
    "#fabed4", "#469990", "#dcbeff", "#9a6324",
]


@dataclass
class ClientState:
    id: str
    color: str
    websocket: WebSocket
    user_id: int
    display_name: str
    in_progress: dict = field(default_factory=dict)


class NotesConnectionManager:
    def __init__(self) -> None:
        self._boards: dict[int, dict[WebSocket, ClientState]] = {}
        self._color_cycle = itertools.cycle(PRESENCE_PALETTE)

    def connect(self, board_id: int, websocket: WebSocket, user_id: int, display_name: str) -> ClientState:
        state = ClientState(
            id=str(uuid.uuid4()), color=next(self._color_cycle),
            websocket=websocket, user_id=user_id, display_name=display_name,
        )
        self._boards.setdefault(board_id, {})[websocket] = state
        return state

    def disconnect(self, board_id: int, websocket: WebSocket) -> Optional[ClientState]:
        clients = self._boards.get(board_id)
        if not clients:
            return None
        state = clients.pop(websocket, None)
        if not clients:
            self._boards.pop(board_id, None)
        return state

    async def broadcast(self, board_id: int, message: dict, exclude: Optional[WebSocket] = None) -> None:
        clients = self._boards.get(board_id)
        if not clients:
            return
        dead = []
        for ws in list(clients.keys()):
            if ws is exclude:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.pop(ws, None)
