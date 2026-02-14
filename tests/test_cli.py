from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from e2epool.cli import main


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def mock_ipc():
    with patch("e2epool.cli.IPCClient") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        yield client


@pytest.fixture
def mock_config():
    with patch("e2epool.cli.load_agent_config") as mock_load:
        from e2epool.agent_config import AgentConfig

        mock_load.return_value = AgentConfig(
            socket_path="/tmp/test.sock",
            runner_id="r1",
            token="t1",
        )
        yield mock_load


class TestCreateCommand:
    def test_create_success(self, cli_runner, mock_ipc, mock_config):
        mock_ipc.request.return_value = {
            "id": "c1",
            "status": "ok",
            "data": {"name": "job-42-1700000000-abcd1234"},
        }
        result = cli_runner.invoke(main, ["create", "--job-id", "42"])
        assert result.exit_code == 0
        assert "job-42-1700000000-abcd1234" in result.output

    def test_create_error(self, cli_runner, mock_ipc, mock_config):
        mock_ipc.request.return_value = {
            "id": "c2",
            "status": "error",
            "error": {"code": 409, "detail": "Active checkpoint exists"},
        }
        result = cli_runner.invoke(main, ["create", "--job-id", "42"])
        assert result.exit_code == 1

    def test_create_agent_not_running(self, cli_runner, mock_ipc, mock_config):
        mock_ipc.request.side_effect = FileNotFoundError("No socket")
        result = cli_runner.invoke(main, ["create", "--job-id", "42"])
        assert result.exit_code == 2
        assert "not running" in result.output


class TestFinalizeCommand:
    def test_finalize_success(self, cli_runner, mock_ipc, mock_config):
        mock_ipc.request.return_value = {
            "id": "f1",
            "status": "ok",
            "data": {"detail": "Finalize queued"},
        }
        result = cli_runner.invoke(
            main,
            ["finalize", "--checkpoint", "job-x-123-aabbccdd", "--status", "success"],
        )
        assert result.exit_code == 0
        assert "Finalize queued" in result.output

    def test_finalize_error(self, cli_runner, mock_ipc, mock_config):
        mock_ipc.request.return_value = {
            "id": "f2",
            "status": "error",
            "error": {"code": 404, "detail": "Checkpoint not found"},
        }
        result = cli_runner.invoke(
            main,
            ["finalize", "--checkpoint", "job-x-123-aabbccdd", "--status", "failure"],
        )
        assert result.exit_code == 1


class TestStatusCommand:
    def test_status_success(self, cli_runner, mock_ipc, mock_config):
        mock_ipc.request.return_value = {
            "id": "s1",
            "status": "ok",
            "data": {
                "name": "job-x-123-aabbccdd",
                "state": "created",
                "finalize_status": None,
            },
        }
        result = cli_runner.invoke(
            main, ["status", "--checkpoint", "job-x-123-aabbccdd"]
        )
        assert result.exit_code == 0
        assert "created" in result.output

    def test_status_not_found(self, cli_runner, mock_ipc, mock_config):
        mock_ipc.request.return_value = {
            "id": "s2",
            "status": "error",
            "error": {"code": 404, "detail": "Checkpoint not found"},
        }
        result = cli_runner.invoke(
            main, ["status", "--checkpoint", "job-x-123-aabbccdd"]
        )
        assert result.exit_code == 1


class TestAgentCommand:
    def test_agent_requires_config(self, cli_runner):
        with patch("e2epool.cli.load_agent_config") as mock_load:
            from e2epool.agent_config import AgentConfig

            mock_load.return_value = AgentConfig()  # No runner_id/token
            result = cli_runner.invoke(main, ["agent"])
            assert result.exit_code == 1
            assert "runner_id and token" in result.output
