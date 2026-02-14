import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from e2epool.agent import Agent
from e2epool.agent_config import AgentConfig


@pytest.fixture
def config(tmp_path):
    return AgentConfig(
        controller_url="ws://localhost:9999/ws/agent",
        runner_id="test-runner",
        token="test-token",
        socket_path=str(tmp_path / "agent.sock"),
        reconnect_max_delay=2,
        heartbeat_interval=60,
    )


class TestAgentInit:
    def test_build_url(self, config):
        agent = Agent(config)
        url = agent._build_url()
        assert "runner_id=test-runner" in url
        assert "token=test-token" in url
        assert url.startswith("ws://localhost:9999/ws/agent")

    def test_build_url_with_existing_query(self, config):
        config.controller_url = "ws://host:8080/ws/agent?extra=1"
        agent = Agent(config)
        url = agent._build_url()
        assert "&runner_id=test-runner" in url


class TestAgentIPCHandler:
    @pytest.mark.asyncio
    async def test_ipc_returns_error_when_disconnected(self, config):
        agent = Agent(config)
        result = await agent._handle_ipc({"id": "x1", "type": "ping"})
        assert result["status"] == "error"
        assert result["error"]["code"] == 503

    @pytest.mark.asyncio
    async def test_ipc_forwards_to_ws(self, config):
        agent = Agent(config)
        mock_ws = AsyncMock()
        agent._ws = mock_ws

        async def fake_send(data):
            msg = json.loads(data)
            # Simulate controller response
            fut = agent._pending.get(msg["id"])
            if fut:
                fut.set_result(
                    {"id": msg["id"], "status": "ok", "data": {"pong": True}}
                )

        mock_ws.send = fake_send

        result = await agent._handle_ipc({"id": "x2", "type": "ping"})
        assert result["status"] == "ok"
        assert result["data"]["pong"] is True


class TestAgentFailPending:
    def test_fail_pending_clears_futures(self, config):
        agent = Agent(config)
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        agent._pending["abc"] = fut
        agent._fail_pending("disconnected")
        assert len(agent._pending) == 0
        assert fut.done()
        loop.close()


class TestAgentExecHandler:
    @pytest.mark.asyncio
    async def test_exec_echo(self, config):
        """Verify _handle_exec runs a shell command and sends result."""
        agent = Agent(config)
        mock_ws = AsyncMock()
        sent = []
        mock_ws.send = AsyncMock(side_effect=lambda data: sent.append(json.loads(data)))

        await agent._handle_exec(
            mock_ws,
            {"id": "e1", "type": "exec", "payload": {"cmd": "echo hello"}},
        )

        assert len(sent) == 1
        msg = sent[0]
        assert msg["id"] == "e1"
        assert msg["status"] == "ok"
        assert msg["data"]["exit_code"] == 0
        assert "hello" in msg["data"]["stdout"]

    @pytest.mark.asyncio
    async def test_exec_nonzero_exit(self, config):
        """Verify _handle_exec reports non-zero exit code."""
        agent = Agent(config)
        mock_ws = AsyncMock()
        sent = []
        mock_ws.send = AsyncMock(side_effect=lambda data: sent.append(json.loads(data)))

        await agent._handle_exec(
            mock_ws,
            {"id": "e2", "type": "exec", "payload": {"cmd": "exit 42", "timeout": 5}},
        )

        msg = sent[0]
        assert msg["id"] == "e2"
        assert msg["status"] == "error"
        assert msg["data"]["exit_code"] == 42

    @pytest.mark.asyncio
    async def test_exec_empty_cmd(self, config):
        """Verify _handle_exec rejects empty commands."""
        agent = Agent(config)
        mock_ws = AsyncMock()
        sent = []
        mock_ws.send = AsyncMock(side_effect=lambda data: sent.append(json.loads(data)))

        await agent._handle_exec(
            mock_ws,
            {"id": "e3", "type": "exec", "payload": {"cmd": "", "timeout": 5}},
        )

        msg = sent[0]
        assert msg["id"] == "e3"
        assert msg["status"] == "error"
        assert "Empty command" in msg["data"]["stderr"]

    @pytest.mark.asyncio
    async def test_exec_timeout(self, config):
        """Verify _handle_exec kills process on timeout."""
        agent = Agent(config)
        mock_ws = AsyncMock()
        sent = []
        mock_ws.send = AsyncMock(side_effect=lambda data: sent.append(json.loads(data)))

        await agent._handle_exec(
            mock_ws,
            {
                "id": "e4",
                "type": "exec",
                "payload": {"cmd": "sleep 60", "timeout": 0.1},
            },
        )

        msg = sent[0]
        assert msg["id"] == "e4"
        assert msg["status"] == "error"
        assert "timed out" in msg["data"]["stderr"]
