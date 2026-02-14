import asyncio
import uuid

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class WSManager:
    """Registry of active WebSocket connections keyed by runner_id."""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, tuple[asyncio.Future, str]] = {}

    async def connect(self, runner_id: str, ws: WebSocket) -> None:
        self._connections[runner_id] = ws

    async def disconnect(self, runner_id: str) -> None:
        self._connections.pop(runner_id, None)
        to_remove = [mid for mid, (_, rid) in self._pending.items() if rid == runner_id]
        for mid in to_remove:
            fut, _ = self._pending.pop(mid)
            if not fut.done():
                fut.set_exception(ConnectionError(f"Agent {runner_id} disconnected"))

    def is_connected(self, runner_id: str) -> bool:
        return runner_id in self._connections

    def connected_runners(self) -> list[str]:
        return list(self._connections.keys())

    async def send_command(
        self, runner_id: str, payload: dict, timeout: float = 120.0
    ) -> dict:
        """Send an exec command to an agent and await the response."""
        ws = self._connections.get(runner_id)
        if ws is None:
            raise ConnectionError(f"Agent {runner_id} not connected")

        msg_id = uuid.uuid4().hex[:12]
        message = {"id": msg_id, "type": "exec", "payload": payload}

        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[msg_id] = (fut, runner_id)

        try:
            await ws.send_json(message)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"Agent {runner_id} did not respond within {timeout}s")
        except Exception:
            self._pending.pop(msg_id, None)
            raise

    def route_response(self, msg_id: str, data: dict) -> bool:
        """Resolve a pending Future for a controller-initiated command."""
        entry = self._pending.pop(msg_id, None)
        if entry is None:
            return False
        fut, _ = entry
        if not fut.done():
            fut.set_result(data)
        return True


ws_manager = WSManager()
