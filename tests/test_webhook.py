"""
Tests for e2epool.routers.webhook — GitLab and GitHub webhook endpoints.
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

from e2epool.models import Checkpoint


@pytest.fixture
def gitlab_secret():
    return "test-gitlab-secret"


@pytest.fixture
def github_secret():
    return "test-github-secret"


@pytest.fixture
def webhook_client(db, mock_inventory, mock_backend, gitlab_secret, github_secret):
    """TestClient with webhook secrets configured."""
    from e2epool.database import get_db
    from e2epool.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("e2epool.main.reconcile_on_startup"),
        patch("e2epool.routers.webhook.settings") as mock_settings,
    ):
        mock_settings.gitlab_webhook_secret = gitlab_secret
        mock_settings.github_webhook_secret = github_secret
        from fastapi.testclient import TestClient

        yield TestClient(app)

    app.dependency_overrides.clear()


def _create_checkpoint(db, job_id="12345", state="created"):
    cp = Checkpoint(
        name=f"job-{job_id}-test",
        runner_id="test-runner-01",
        job_id=str(job_id),
        state=state,
    )
    db.add(cp)
    db.commit()
    db.refresh(cp)
    return cp


class TestGitLabWebhook:
    """Tests for POST /webhooks/gitlab."""

    @patch("e2epool.routers.webhook.do_finalize")
    def test_gitlab_webhook_triggers_finalize(
        self, mock_finalize, webhook_client, db, gitlab_secret
    ):
        cp = _create_checkpoint(db, job_id="12345")

        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "build",
                "build_id": 12345,
                "build_status": "success",
            },
            headers={"X-Gitlab-Token": gitlab_secret},
        )

        assert resp.status_code == 200
        db.refresh(cp)
        assert cp.state == "finalize_queued"
        assert cp.finalize_status == "success"
        assert cp.finalize_source == "webhook"
        mock_finalize.delay.assert_called_once_with(cp.name)

    @patch("e2epool.routers.webhook.do_finalize")
    def test_gitlab_webhook_failed_status(
        self, mock_finalize, webhook_client, db, gitlab_secret
    ):
        cp = _create_checkpoint(db, job_id="99999")

        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "build",
                "build_id": 99999,
                "build_status": "failed",
            },
            headers={"X-Gitlab-Token": gitlab_secret},
        )

        assert resp.status_code == 200
        db.refresh(cp)
        assert cp.state == "finalize_queued"
        assert cp.finalize_status == "failure"
        mock_finalize.delay.assert_called_once()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_gitlab_webhook_canceled_status(
        self, mock_finalize, webhook_client, db, gitlab_secret
    ):
        cp = _create_checkpoint(db, job_id="88888")

        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "build",
                "build_id": 88888,
                "build_status": "canceled",
            },
            headers={"X-Gitlab-Token": gitlab_secret},
        )

        assert resp.status_code == 200
        db.refresh(cp)
        assert cp.state == "finalize_queued"
        assert cp.finalize_status == "canceled"
        mock_finalize.delay.assert_called_once()

    def test_gitlab_webhook_invalid_token_returns_403(self, webhook_client, db):
        _create_checkpoint(db)

        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "build",
                "build_id": 12345,
                "build_status": "success",
            },
            headers={"X-Gitlab-Token": "wrong-token"},
        )

        assert resp.status_code == 403

    def test_gitlab_webhook_missing_token_returns_403(self, webhook_client, db):
        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "build",
                "build_id": 12345,
                "build_status": "success",
            },
        )

        assert resp.status_code == 403

    @patch("e2epool.routers.webhook.do_finalize")
    def test_gitlab_webhook_no_checkpoint_returns_200(
        self, mock_finalize, webhook_client, gitlab_secret
    ):
        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "build",
                "build_id": 99999,
                "build_status": "success",
            },
            headers={"X-Gitlab-Token": gitlab_secret},
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_gitlab_webhook_already_finalized_returns_200(
        self, mock_finalize, webhook_client, db, gitlab_secret
    ):
        _create_checkpoint(db, job_id="12345", state="finalize_queued")

        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "build",
                "build_id": 12345,
                "build_status": "success",
            },
            headers={"X-Gitlab-Token": gitlab_secret},
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_gitlab_webhook_non_terminal_status_ignored(
        self, mock_finalize, webhook_client, db, gitlab_secret
    ):
        _create_checkpoint(db, job_id="12345")

        for status in ("running", "pending", "created"):
            resp = webhook_client.post(
                "/webhooks/gitlab",
                json={
                    "object_kind": "build",
                    "build_id": 12345,
                    "build_status": status,
                },
                headers={"X-Gitlab-Token": gitlab_secret},
            )

            assert resp.status_code == 200

        mock_finalize.delay.assert_not_called()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_gitlab_webhook_non_build_event_ignored(
        self, mock_finalize, webhook_client, db, gitlab_secret
    ):
        _create_checkpoint(db, job_id="12345")

        resp = webhook_client.post(
            "/webhooks/gitlab",
            json={
                "object_kind": "pipeline",
                "pipeline_id": 12345,
            },
            headers={"X-Gitlab-Token": gitlab_secret},
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()


class TestGitHubWebhook:
    """Tests for POST /webhooks/github."""

    def _sign(self, body: bytes, secret: str) -> str:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={sig}"

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_triggers_finalize(
        self, mock_finalize, webhook_client, db, github_secret
    ):
        cp = _create_checkpoint(db, job_id="67890")

        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 67890,
                "conclusion": "success",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        db.refresh(cp)
        assert cp.state == "finalize_queued"
        assert cp.finalize_status == "success"
        assert cp.finalize_source == "webhook"
        mock_finalize.delay.assert_called_once_with(cp.name)

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_failure_conclusion(
        self, mock_finalize, webhook_client, db, github_secret
    ):
        cp = _create_checkpoint(db, job_id="67891")

        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 67891,
                "conclusion": "failure",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        db.refresh(cp)
        assert cp.finalize_status == "failure"
        mock_finalize.delay.assert_called_once()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_cancelled_conclusion(
        self, mock_finalize, webhook_client, db, github_secret
    ):
        cp = _create_checkpoint(db, job_id="67892")

        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 67892,
                "conclusion": "cancelled",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        db.refresh(cp)
        assert cp.finalize_status == "canceled"
        mock_finalize.delay.assert_called_once()

    def test_github_webhook_invalid_signature_returns_403(
        self, webhook_client, db, github_secret
    ):
        _create_checkpoint(db, job_id="67890")

        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 67890,
                "conclusion": "success",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=invalidsignature",
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 403

    def test_github_webhook_missing_signature_returns_403(self, webhook_client, db):
        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 67890,
                "conclusion": "success",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 403

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_wrong_event_ignored(
        self, mock_finalize, webhook_client, db, github_secret
    ):
        _create_checkpoint(db, job_id="67890")

        payload = {"action": "completed", "check_run": {"id": 67890}}
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "check_run",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_non_completed_action_ignored(
        self, mock_finalize, webhook_client, db, github_secret
    ):
        _create_checkpoint(db, job_id="67890")

        payload = {
            "action": "in_progress",
            "workflow_job": {
                "id": 67890,
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_no_checkpoint_returns_200(
        self, mock_finalize, webhook_client, github_secret
    ):
        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 99999,
                "conclusion": "success",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_already_finalized_returns_200(
        self, mock_finalize, webhook_client, db, github_secret
    ):
        _create_checkpoint(db, job_id="67890", state="finalize_queued")

        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 67890,
                "conclusion": "success",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()

    @patch("e2epool.routers.webhook.do_finalize")
    def test_github_webhook_skipped_conclusion_ignored(
        self, mock_finalize, webhook_client, db, github_secret
    ):
        _create_checkpoint(db, job_id="67890")

        payload = {
            "action": "completed",
            "workflow_job": {
                "id": 67890,
                "conclusion": "skipped",
            },
        }
        body = json.dumps(payload).encode()

        resp = webhook_client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": self._sign(body, github_secret),
                "X-GitHub-Event": "workflow_job",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        mock_finalize.delay.assert_not_called()
