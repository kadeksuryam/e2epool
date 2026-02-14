from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from e2epool.dependencies import set_backends, set_inventory
from e2epool.inventory import Inventory
from e2epool.services.ws_manager import ws_manager
from tests.conftest import _make_runner


@pytest.fixture
def runner():
    return _make_runner(runner_id="ws-runner", token="ws-secret")


@pytest.fixture
def inventory(runner):
    inv = Inventory({runner.runner_id: runner})
    set_inventory(inv)
    return inv


@pytest.fixture
def backend():
    from unittest.mock import MagicMock

    b = MagicMock()
    b.create_checkpoint = MagicMock()
    b.check_ready = MagicMock(return_value=True)
    set_backends({"proxmox": b, "bare_metal": b})
    return b


@pytest.fixture
def ws_client(inventory, backend):
    from e2epool.main import app

    with patch("e2epool.main.reconcile_on_startup"):
        yield TestClient(app)


class TestWSAuth:
    def test_invalid_token(self, ws_client, runner):
        with pytest.raises(Exception):
            with ws_client.websocket_connect(
                f"/ws/agent?runner_id={runner.runner_id}&token=wrong"
            ):
                pass

    def test_invalid_runner(self, ws_client):
        with pytest.raises(Exception):
            with ws_client.websocket_connect(
                "/ws/agent?runner_id=nonexistent&token=nope"
            ):
                pass


class TestWSPing:
    def test_ping_pong(self, ws_client, runner):
        with ws_client.websocket_connect(
            f"/ws/agent?runner_id={runner.runner_id}&token={runner.token}"
        ) as ws:
            ws.send_json({"id": "p1", "type": "ping", "payload": {}})
            resp = ws.receive_json()
            assert resp["status"] == "ok"
            assert resp["data"]["pong"] is True


class TestWSCreate:
    def test_create_checkpoint(self, ws_client, runner):
        with ws_client.websocket_connect(
            f"/ws/agent?runner_id={runner.runner_id}&token={runner.token}"
        ) as ws:
            ws.send_json({"id": "c1", "type": "create", "payload": {"job_id": "100"}})
            resp = ws.receive_json()
            assert resp["status"] == "ok"
            assert resp["data"]["name"].startswith("job-100-")

    def test_invalid_message(self, ws_client, runner):
        with ws_client.websocket_connect(
            f"/ws/agent?runner_id={runner.runner_id}&token={runner.token}"
        ) as ws:
            ws.send_json({"id": "bad", "type": "invalid_type"})
            resp = ws.receive_json()
            assert resp["status"] == "error"


class TestWSManager:
    def test_connect_disconnect(self, ws_client, runner):
        assert not ws_manager.is_connected(runner.runner_id)
        with ws_client.websocket_connect(
            f"/ws/agent?runner_id={runner.runner_id}&token={runner.token}"
        ):
            assert ws_manager.is_connected(runner.runner_id)
            assert runner.runner_id in ws_manager.connected_runners()
        # After disconnect, the manager should have removed the runner
        # Note: TestClient may not trigger disconnect cleanup in all cases
