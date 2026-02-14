from unittest.mock import patch

import pytest

from e2epool.schemas import WSRequest
from e2epool.services.ws_handler import handle_message
from tests.conftest import _make_runner


@pytest.fixture
def runner():
    return _make_runner()


class TestHandlePing:
    def test_ping_returns_pong(self, db, runner):
        req = WSRequest(id="abc", type="ping")
        resp = handle_message(req, runner, db)
        assert resp.status == "ok"
        assert resp.data == {"pong": True}
        assert resp.id == "abc"


class TestHandleCreate:
    def test_create_success(self, db, runner, mock_backend):
        req = WSRequest(id="c1", type="create", payload={"job_id": "42"})
        resp = handle_message(req, runner, db)
        assert resp.status == "ok"
        assert resp.data["name"].startswith("job-42-")
        assert resp.data["runner_id"] == runner.runner_id

    def test_create_missing_job_id(self, db, runner):
        req = WSRequest(id="c2", type="create", payload={})
        resp = handle_message(req, runner, db)
        assert resp.status == "error"
        assert resp.error["code"] == 400

    def test_create_conflict(self, db, runner, mock_backend):
        req = WSRequest(id="c3", type="create", payload={"job_id": "42"})
        handle_message(req, runner, db)
        req2 = WSRequest(id="c4", type="create", payload={"job_id": "43"})
        resp2 = handle_message(req2, runner, db)
        assert resp2.status == "error"
        assert resp2.error["code"] == 409


class TestHandleFinalize:
    def _create(self, db, runner, mock_backend, job_id="99"):
        req = WSRequest(id="f0", type="create", payload={"job_id": job_id})
        return handle_message(req, runner, db)

    @patch("e2epool.services.ws_handler.do_finalize")
    def test_finalize_success(self, mock_task, db, runner, mock_backend):
        created = self._create(db, runner, mock_backend)
        name = created.data["name"]

        req = WSRequest(
            id="f1",
            type="finalize",
            payload={
                "checkpoint_name": name,
                "status": "success",
                "source": "agent",
            },
        )
        resp = handle_message(req, runner, db)
        assert resp.status == "ok"
        assert "Finalize queued" in resp.data["detail"]
        mock_task.delay.assert_called_once_with(name)

    def test_finalize_missing_fields(self, db, runner):
        req = WSRequest(id="f2", type="finalize", payload={})
        resp = handle_message(req, runner, db)
        assert resp.status == "error"
        assert resp.error["code"] == 400

    def test_finalize_not_found(self, db, runner):
        req = WSRequest(
            id="f3",
            type="finalize",
            payload={
                "checkpoint_name": "job-nope-1234567890-deadbeef",
                "status": "success",
            },
        )
        resp = handle_message(req, runner, db)
        assert resp.status == "error"
        assert resp.error["code"] == 404

    @patch("e2epool.services.ws_handler.do_finalize")
    def test_finalize_broker_unavailable(self, mock_task, db, runner, mock_backend):
        created = self._create(db, runner, mock_backend)
        name = created.data["name"]
        mock_task.delay.side_effect = ConnectionError("broker down")

        req = WSRequest(
            id="f5",
            type="finalize",
            payload={
                "checkpoint_name": name,
                "status": "success",
                "source": "agent",
            },
        )
        resp = handle_message(req, runner, db)
        assert resp.status == "error"
        assert resp.error["code"] == 503

    @patch("e2epool.services.ws_handler.do_finalize")
    def test_finalize_wrong_runner(self, mock_task, db, runner, mock_backend):
        created = self._create(db, runner, mock_backend)
        name = created.data["name"]

        other = _make_runner(runner_id="other-runner", token="other-tok")
        req = WSRequest(
            id="f4",
            type="finalize",
            payload={"checkpoint_name": name, "status": "success"},
        )
        resp = handle_message(req, other, db)
        assert resp.status == "error"
        assert resp.error["code"] == 403


class TestHandleStatus:
    def test_status_success(self, db, runner, mock_backend):
        req = WSRequest(id="s0", type="create", payload={"job_id": "50"})
        created = handle_message(req, runner, db)
        name = created.data["name"]

        req2 = WSRequest(id="s1", type="status", payload={"checkpoint_name": name})
        resp = handle_message(req2, runner, db)
        assert resp.status == "ok"
        assert resp.data["state"] == "created"

    def test_status_not_found(self, db, runner):
        req = WSRequest(
            id="s2",
            type="status",
            payload={"checkpoint_name": "job-nope-1234567890-deadbeef"},
        )
        resp = handle_message(req, runner, db)
        assert resp.status == "error"
        assert resp.error["code"] == 404

    def test_status_missing_name(self, db, runner):
        req = WSRequest(id="s3", type="status", payload={})
        resp = handle_message(req, runner, db)
        assert resp.status == "error"
        assert resp.error["code"] == 400
