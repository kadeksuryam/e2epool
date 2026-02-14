import asyncio
import json
import struct
from pathlib import Path

import pytest

from e2epool.ipc import (
    HEADER_FMT,
    MAX_MSG_SIZE,
    IPCClient,
    IPCServer,
    recv_msg,
    recv_msg_sync,
    send_msg_sync,
)


@pytest.fixture
def socket_path():
    """Short socket path to avoid macOS 104-char AF_UNIX limit."""
    import tempfile

    d = tempfile.mkdtemp(prefix="ipc")
    p = Path(d) / "t.sock"
    yield str(p)
    p.unlink(missing_ok=True)
    Path(d).rmdir()


class TestLengthPrefixedProtocol:
    @pytest.mark.asyncio
    async def test_send_recv_async(self):
        reader = asyncio.StreamReader()

        data = {"id": "1", "type": "ping"}
        payload = json.dumps(data).encode()
        frame = struct.pack(HEADER_FMT, len(payload)) + payload
        reader.feed_data(frame)

        result = await recv_msg(reader)
        assert result == data

    def test_send_recv_sync(self):
        import socket

        s1, s2 = socket.socketpair()
        try:
            data = {"id": "2", "type": "create", "payload": {"job_id": "x"}}
            send_msg_sync(s1, data)
            result = recv_msg_sync(s2)
            assert result == data
        finally:
            s1.close()
            s2.close()

    def test_recv_sync_eof(self):
        import socket

        s1, s2 = socket.socketpair()
        s1.close()
        result = recv_msg_sync(s2)
        assert result is None
        s2.close()

    def test_recv_sync_rejects_oversized_message(self):
        import socket

        s1, s2 = socket.socketpair()
        try:
            # Send a header claiming a payload larger than MAX_MSG_SIZE
            header = struct.pack(HEADER_FMT, MAX_MSG_SIZE + 1)
            s1.sendall(header)
            with pytest.raises(ValueError, match="exceeds maximum"):
                recv_msg_sync(s2)
        finally:
            s1.close()
            s2.close()


class TestIPCServerClient:
    @pytest.mark.asyncio
    async def test_roundtrip(self, socket_path):
        async def echo_handler(msg):
            return {"id": msg["id"], "status": "ok", "data": msg}

        server = IPCServer(socket_path, echo_handler)
        await server.start()

        try:
            loop = asyncio.get_event_loop()
            client = IPCClient(socket_path, timeout=5.0)
            result = await loop.run_in_executor(
                None, client.request, {"id": "t1", "type": "ping"}
            )
            assert result["status"] == "ok"
            assert result["data"]["id"] == "t1"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_removes_socket_on_stop(self, socket_path):
        async def noop(msg):
            return {}

        server = IPCServer(socket_path, noop)
        await server.start()
        assert Path(socket_path).exists()
        await server.stop()
        assert not Path(socket_path).exists()

    def test_client_raises_on_missing_socket(self, socket_path):
        client = IPCClient(socket_path)
        with pytest.raises(FileNotFoundError):
            client.request({"id": "x"})
