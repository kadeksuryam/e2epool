"""WebSocket agent daemon: connects to controller, serves CLI via IPC."""

import asyncio
import json
import random
import signal
import uuid

import structlog
import websockets

from e2epool.agent_config import AgentConfig
from e2epool.ipc import IPCServer

logger = structlog.get_logger()


class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ipc: IPCServer | None = None
        self._shutdown = asyncio.Event()
        self._connected = asyncio.Event()

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        self._ipc = IPCServer(self.config.socket_path, self._handle_ipc)
        await self._ipc.start()
        logger.info("IPC server started", socket=self.config.socket_path)

        try:
            await self._ws_loop()
        finally:
            await self._ipc.stop()
            logger.info("Agent stopped")

    def _handle_signal(self) -> None:
        logger.info("Shutdown signal received")
        self._shutdown.set()

    async def _ws_loop(self) -> None:
        delay = 1.0
        while not self._shutdown.is_set():
            try:
                url = self._build_url()
                logger.info("Connecting to controller", url=url)
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    self._connected.set()
                    delay = 1.0
                    logger.info("Connected to controller")

                    heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    shutdown_task = asyncio.create_task(self._shutdown.wait())

                    done, pending = await asyncio.wait(
                        [heartbeat_task, recv_task, shutdown_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass

                    if self._shutdown.is_set():
                        return

            except (
                websockets.ConnectionClosed,
                OSError,
                ConnectionRefusedError,
            ) as e:
                logger.warning("WS connection lost", error=str(e))
            except Exception:
                logger.exception("Unexpected WS error")
            finally:
                self._ws = None
                self._connected.clear()
                self._fail_pending("Connection lost")

            if not self._shutdown.is_set():
                jitter = random.uniform(0, delay * 0.1)
                logger.info("Reconnecting", delay=f"{delay + jitter:.1f}s")
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(), timeout=delay + jitter
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, self.config.reconnect_max_delay)

    def _build_url(self) -> str:
        base = self.config.controller_url.rstrip("/")
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}runner_id={self.config.runner_id}&token={self.config.token}"

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_interval)
            msg_id = uuid.uuid4().hex[:8]
            await ws.send(f'{{"id":"{msg_id}","type":"ping","payload":{{}}}}')

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            # Controller-initiated exec command
            if data.get("type") == "exec":
                asyncio.create_task(self._handle_exec(ws, data))
                continue

            # Response to an agent-initiated request
            msg_id = data.get("id", "")
            fut = self._pending.pop(msg_id, None)
            if fut and not fut.done():
                fut.set_result(data)

    async def _handle_exec(self, ws, request: dict) -> None:
        """Execute a shell command and send the result back."""
        msg_id = request.get("id", "")
        payload = request.get("payload", {})
        cmd = payload.get("cmd", "")
        timeout = payload.get("timeout", 120)

        if not cmd:
            await ws.send(
                json.dumps(
                    {
                        "id": msg_id,
                        "status": "error",
                        "data": {
                            "exit_code": -1,
                            "stdout": "",
                            "stderr": "Empty command",
                        },
                    }
                )
            )
            return

        max_output = 65536  # 64KB
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                await ws.send(
                    json.dumps(
                        {
                            "id": msg_id,
                            "status": "error",
                            "data": {
                                "exit_code": -1,
                                "stdout": "",
                                "stderr": f"Command timed out after {timeout}s",
                            },
                        }
                    )
                )
                return

            stdout = stdout_bytes.decode(errors="replace")[:max_output]
            stderr = stderr_bytes.decode(errors="replace")[:max_output]
            exit_code = proc.returncode

            status = "ok" if exit_code == 0 else "error"
            await ws.send(
                json.dumps(
                    {
                        "id": msg_id,
                        "status": status,
                        "data": {
                            "exit_code": exit_code,
                            "stdout": stdout,
                            "stderr": stderr,
                        },
                    }
                )
            )
        except Exception as e:
            logger.exception("exec handler failed", cmd=cmd)
            await ws.send(
                json.dumps(
                    {
                        "id": msg_id,
                        "status": "error",
                        "data": {
                            "exit_code": -1,
                            "stdout": "",
                            "stderr": str(e),
                        },
                    }
                )
            )

    async def _send_and_wait(self, request: dict, timeout: float = 30.0) -> dict:
        if not self._ws:
            raise ConnectionError("Not connected to controller")

        msg_id = request["id"]
        fut = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

        await self._ws.send(json.dumps(request))

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError("Controller did not respond in time")

    async def _handle_ipc(self, msg: dict) -> dict:
        """Handle incoming IPC request from CLI."""
        if not self._ws:
            return {
                "id": msg.get("id", ""),
                "status": "error",
                "error": {"code": 503, "detail": "Not connected to controller"},
            }
        try:
            return await self._send_and_wait(msg)
        except (ConnectionError, TimeoutError) as e:
            return {
                "id": msg.get("id", ""),
                "status": "error",
                "error": {"code": 503, "detail": str(e)},
            }

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
        self._pending.clear()
