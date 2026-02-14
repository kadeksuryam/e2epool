"""IPC over Unix domain socket using length-prefixed JSON messages."""

import asyncio
import json
import struct
from pathlib import Path

HEADER_FMT = "!I"  # network-order unsigned 4-byte int
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAX_MSG_SIZE = 1_048_576  # 1 MB


async def send_msg(writer: asyncio.StreamWriter, data: dict) -> None:
    """Send a length-prefixed JSON message."""
    payload = json.dumps(data).encode()
    writer.write(struct.pack(HEADER_FMT, len(payload)) + payload)
    await writer.drain()


async def recv_msg(reader: asyncio.StreamReader) -> dict | None:
    """Receive a length-prefixed JSON message. Returns None on EOF."""
    header = await reader.readexactly(HEADER_SIZE)
    (length,) = struct.unpack(HEADER_FMT, header)
    if length > MAX_MSG_SIZE:
        raise ValueError(f"Message size {length} exceeds maximum {MAX_MSG_SIZE}")
    payload = await reader.readexactly(length)
    return json.loads(payload)


def send_msg_sync(sock, data: dict) -> None:
    """Blocking send of a length-prefixed JSON message."""
    payload = json.dumps(data).encode()
    sock.sendall(struct.pack(HEADER_FMT, len(payload)) + payload)


def recv_msg_sync(sock) -> dict | None:
    """Blocking receive of a length-prefixed JSON message."""
    header = _recvall(sock, HEADER_SIZE)
    if header is None:
        return None
    (length,) = struct.unpack(HEADER_FMT, header)
    if length > MAX_MSG_SIZE:
        raise ValueError(f"Message size {length} exceeds maximum {MAX_MSG_SIZE}")
    payload = _recvall(sock, length)
    if payload is None:
        return None
    return json.loads(payload)


def _recvall(sock, n: int) -> bytes | None:
    """Read exactly n bytes from a socket."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class IPCServer:
    """Async Unix domain socket server that routes requests to a callback."""

    def __init__(self, socket_path: str, handler):
        self.socket_path = socket_path
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        path = Path(self.socket_path)
        path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._on_connect, path=str(path))
        path.chmod(0o660)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        Path(self.socket_path).unlink(missing_ok=True)

    async def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            msg = await recv_msg(reader)
            if msg is not None:
                response = await self._handler(msg)
                await send_msg(writer, response)
        except asyncio.IncompleteReadError:
            pass
        except Exception:
            try:
                await send_msg(
                    writer,
                    {"id": "", "status": "error", "error": "IPC handler error"},
                )
            except Exception:
                pass
        finally:
            writer.close()
            await writer.wait_closed()


class IPCClient:
    """Blocking Unix domain socket client for CLI commands."""

    def __init__(self, socket_path: str, timeout: float = 30.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def request(self, data: dict) -> dict:
        """Send a request and return the response. Raises on failure."""
        import socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
            send_msg_sync(sock, data)
            response = recv_msg_sync(sock)
            if response is None:
                raise ConnectionError("Agent closed connection")
            return response
        finally:
            sock.close()
