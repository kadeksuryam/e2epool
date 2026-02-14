"""Tests for the admin runner CRUD API."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from e2epool.database import get_db
from e2epool.main import app
from e2epool.models import Runner

ADMIN_TOKEN = "test-admin-token"
AUTH_HEADER = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
def admin_client(db):
    """TestClient wired to the transactional test DB session."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with patch("e2epool.main.reconcile_on_startup"):
        yield TestClient(app)
    app.dependency_overrides.clear()


def _proxmox_payload(**overrides):
    base = {
        "runner_id": "api-proxmox-01",
        "backend": "proxmox",
        "proxmox_host": "10.0.0.1",
        "proxmox_user": "root@pam",
        "proxmox_token_name": "e2e",
        "proxmox_token_value": "secret-value",
        "proxmox_node": "pve1",
        "proxmox_vmid": 100,
        "gitlab_runner_id": 42,
        "tags": ["e2e", "proxmox"],
    }
    base.update(overrides)
    return base


def _bare_metal_payload(**overrides):
    base = {
        "runner_id": "api-bare-01",
        "backend": "bare_metal",
        "reset_cmd": "/opt/reset.sh",
        "cleanup_cmd": "/opt/cleanup.sh",
        "tags": ["e2e"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAdminAuth:
    def test_missing_auth_header(self, admin_client):
        resp = admin_client.get("/api/runners")
        assert resp.status_code == 422  # FastAPI validation for missing header

    def test_invalid_auth_format(self, admin_client):
        resp = admin_client.get(
            "/api/runners", headers={"Authorization": "Basic abc"}
        )
        assert resp.status_code == 401

    def test_wrong_token(self, admin_client):
        resp = admin_client.get(
            "/api/runners", headers={"Authorization": "Bearer wrong-token"}
        )
        assert resp.status_code == 403

    def test_empty_bearer_token(self, admin_client):
        resp = admin_client.get(
            "/api/runners", headers={"Authorization": "Bearer "}
        )
        assert resp.status_code == 403

    def test_valid_token(self, admin_client):
        resp = admin_client.get("/api/runners", headers=AUTH_HEADER)
        assert resp.status_code == 200

    @patch("e2epool.dependencies.settings")
    def test_admin_not_configured(self, mock_settings, admin_client):
        mock_settings.admin_token = None
        resp = admin_client.get(
            "/api/runners", headers={"Authorization": "Bearer anything"}
        )
        assert resp.status_code == 503

    def test_auth_required_on_all_methods(self, admin_client):
        """Every admin endpoint requires auth."""
        for method, path in [
            ("GET", "/api/runners"),
            ("POST", "/api/runners"),
            ("GET", "/api/runners/some-id"),
            ("DELETE", "/api/runners/some-id"),
        ]:
            resp = admin_client.request(
                method, path, headers={"Authorization": "Bearer wrong"}
            )
            assert resp.status_code == 403, f"{method} {path} should require auth"


# ---------------------------------------------------------------------------
# POST /api/runners
# ---------------------------------------------------------------------------


class TestCreateRunner:
    def test_create_proxmox_runner(self, admin_client):
        resp = admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["runner_id"] == "api-proxmox-01"
        assert data["backend"] == "proxmox"
        assert "token" in data
        assert len(data["token"]) > 20
        assert data["proxmox_host"] == "10.0.0.1"
        assert data["tags"] == ["e2e", "proxmox"]
        assert data["is_active"] is True
        assert "created_at" in data
        assert "updated_at" in data
        # proxmox_token_value should NOT be in response
        assert "proxmox_token_value" not in data

    def test_create_bare_metal_runner(self, admin_client):
        resp = admin_client.post(
            "/api/runners", json=_bare_metal_payload(), headers=AUTH_HEADER
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["runner_id"] == "api-bare-01"
        assert data["backend"] == "bare_metal"
        assert data["reset_cmd"] == "/opt/reset.sh"

    def test_create_runner_with_no_tags(self, admin_client):
        payload = _bare_metal_payload(runner_id="no-tags-runner")
        del payload["tags"]
        resp = admin_client.post(
            "/api/runners", json=payload, headers=AUTH_HEADER
        )
        assert resp.status_code == 201
        assert resp.json()["tags"] == []

    def test_create_runner_with_empty_tags(self, admin_client):
        resp = admin_client.post(
            "/api/runners",
            json=_bare_metal_payload(runner_id="empty-tags", tags=[]),
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        assert resp.json()["tags"] == []

    def test_create_runner_minimal_fields(self, admin_client):
        """Only required fields, all optional fields omitted."""
        resp = admin_client.post(
            "/api/runners",
            json={
                "runner_id": "minimal-bare",
                "backend": "bare_metal",
                "reset_cmd": "/reset.sh",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["proxmox_host"] is None
        assert data["gitlab_runner_id"] is None
        assert data["tags"] == []

    def test_duplicate_runner_returns_409(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        resp = admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_invalid_backend_returns_422(self, admin_client):
        resp = admin_client.post(
            "/api/runners",
            json={"runner_id": "bad", "backend": "docker"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_missing_proxmox_fields_returns_422(self, admin_client):
        resp = admin_client.post(
            "/api/runners",
            json={"runner_id": "bad", "backend": "proxmox"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_missing_bare_metal_reset_cmd_returns_422(self, admin_client):
        resp = admin_client.post(
            "/api/runners",
            json={"runner_id": "bad", "backend": "bare_metal"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_invalid_runner_id_pattern_returns_422(self, admin_client):
        resp = admin_client.post(
            "/api/runners",
            json={"runner_id": "bad runner!", "backend": "bare_metal", "reset_cmd": "/r"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_empty_runner_id_returns_422(self, admin_client):
        resp = admin_client.post(
            "/api/runners",
            json={"runner_id": "", "backend": "bare_metal", "reset_cmd": "/r"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_missing_runner_id_returns_422(self, admin_client):
        resp = admin_client.post(
            "/api/runners",
            json={"backend": "bare_metal", "reset_cmd": "/r"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/runners
# ---------------------------------------------------------------------------


class TestListRunners:
    def test_list_empty(self, admin_client):
        resp = admin_client.get("/api/runners", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_created_runners(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        admin_client.post(
            "/api/runners", json=_bare_metal_payload(), headers=AUTH_HEADER
        )

        resp = admin_client.get("/api/runners", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = [r["runner_id"] for r in data]
        assert "api-proxmox-01" in ids
        assert "api-bare-01" in ids
        # token and proxmox_token_value must NOT appear in list response
        for r in data:
            assert "token" not in r
            assert "proxmox_token_value" not in r

    def test_list_excludes_inactive(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        admin_client.delete("/api/runners/api-proxmox-01", headers=AUTH_HEADER)

        resp = admin_client.get("/api/runners", headers=AUTH_HEADER)
        assert len(resp.json()) == 0

    def test_list_includes_inactive(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        admin_client.delete("/api/runners/api-proxmox-01", headers=AUTH_HEADER)

        resp = admin_client.get(
            "/api/runners?include_inactive=true", headers=AUTH_HEADER
        )
        data = resp.json()
        assert len(data) == 1
        assert data[0]["is_active"] is False

    def test_list_preserves_tags(self, admin_client):
        """Tags should come back as a list, not a JSON string."""
        admin_client.post(
            "/api/runners",
            json=_proxmox_payload(tags=["tag-a", "tag-b"]),
            headers=AUTH_HEADER,
        )
        resp = admin_client.get("/api/runners", headers=AUTH_HEADER)
        assert resp.json()[0]["tags"] == ["tag-a", "tag-b"]


# ---------------------------------------------------------------------------
# GET /api/runners/{runner_id}
# ---------------------------------------------------------------------------


class TestGetRunner:
    def test_get_existing_runner(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )

        resp = admin_client.get("/api/runners/api-proxmox-01", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["runner_id"] == "api-proxmox-01"
        assert "token" not in data
        assert "proxmox_token_value" not in data

    def test_get_nonexistent_returns_404(self, admin_client):
        resp = admin_client.get("/api/runners/nonexistent", headers=AUTH_HEADER)
        assert resp.status_code == 404

    def test_get_deactivated_runner_returns_404(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        admin_client.delete("/api/runners/api-proxmox-01", headers=AUTH_HEADER)

        resp = admin_client.get("/api/runners/api-proxmox-01", headers=AUTH_HEADER)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/runners/{runner_id}
# ---------------------------------------------------------------------------


class TestDeleteRunner:
    def test_deactivate_runner(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )

        resp = admin_client.delete("/api/runners/api-proxmox-01", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert "deactivated" in resp.json()["detail"]

    def test_deactivate_nonexistent_returns_404(self, admin_client):
        resp = admin_client.delete("/api/runners/nonexistent", headers=AUTH_HEADER)
        assert resp.status_code == 404

    def test_double_deactivate_returns_404(self, admin_client):
        admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        admin_client.delete("/api/runners/api-proxmox-01", headers=AUTH_HEADER)

        resp = admin_client.delete("/api/runners/api-proxmox-01", headers=AUTH_HEADER)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration: created runner token works for agent auth
# ---------------------------------------------------------------------------


class TestCreatedRunnerCanAuthenticate:
    def test_created_runner_token_works_for_http_auth(self, admin_client):
        create_resp = admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        token = create_resp.json()["token"]

        resp = admin_client.get(
            "/runner/readiness",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Should not be 401/403 — the token should be accepted
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_deactivated_runner_token_rejected(self, admin_client):
        """After deactivation, the runner's token should no longer authenticate."""
        create_resp = admin_client.post(
            "/api/runners", json=_proxmox_payload(), headers=AUTH_HEADER
        )
        token = create_resp.json()["token"]

        admin_client.delete("/api/runners/api-proxmox-01", headers=AUTH_HEADER)

        resp = admin_client.get(
            "/runner/readiness",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
