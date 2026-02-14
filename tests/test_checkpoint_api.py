from unittest.mock import patch

AUTH_HEADER = {"Authorization": "Bearer test-token-01"}
AUTH_HEADER_BARE = {"Authorization": "Bearer test-token-bare-01"}


def test_post_create_201(client):
    """Valid token and body returns 201 with checkpoint name and state=created."""
    response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-123"},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"].startswith("job-")
    assert data["runner_id"] == "test-runner-01"
    assert data["job_id"] == "job-123"
    assert data["state"] == "created"
    assert data["finalize_status"] is None
    assert data["created_at"] is not None


def test_post_create_401_no_token(client):
    """Missing Authorization header returns 422 (FastAPI validation error)."""
    response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-123"},
    )
    assert response.status_code == 422
    assert "detail" in response.json()


def test_post_create_403_wrong_token(client):
    """Invalid token returns 403."""
    response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-123"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 403
    assert "Invalid token" in response.json()["detail"]


def test_post_create_409_active_checkpoint(client):
    """Creating checkpoint twice for same runner returns 409."""
    body = {"runner_id": "test-runner-01", "job_id": "job-123"}

    # First request succeeds
    response1 = client.post(
        "/checkpoint/create",
        json=body,
        headers=AUTH_HEADER,
    )
    assert response1.status_code == 201

    # Second request with same runner returns 409
    response2 = client.post(
        "/checkpoint/create",
        json=body,
        headers=AUTH_HEADER,
    )
    assert response2.status_code == 409
    assert "active checkpoint" in response2.json()["detail"].lower()


def test_post_create_422_missing_fields(client):
    """Empty body returns 422 validation error."""
    response = client.post(
        "/checkpoint/create",
        json={},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 422
    assert "detail" in response.json()


def test_post_finalize_202(client):
    """Finalizing an existing checkpoint returns 202 and queues task."""
    # Create checkpoint first
    create_response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-456"},
        headers=AUTH_HEADER,
    )
    assert create_response.status_code == 201
    checkpoint_name = create_response.json()["name"]

    # Mock the Celery task
    with patch("e2epool.routers.checkpoint.do_finalize.delay") as mock_delay:
        response = client.post(
            "/checkpoint/finalize",
            json={"checkpoint_name": checkpoint_name, "status": "success"},
            headers=AUTH_HEADER,
        )
        assert response.status_code == 202
        data = response.json()
        assert "queued" in data["detail"].lower()
        assert data["checkpoint_name"] == checkpoint_name

        # Verify task was queued
        mock_delay.assert_called_once_with(checkpoint_name)


def test_post_finalize_202_idempotent(client):
    """Finalizing twice returns 202 both times (idempotent)."""
    # Create checkpoint
    create_response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-789"},
        headers=AUTH_HEADER,
    )
    assert create_response.status_code == 201
    checkpoint_name = create_response.json()["name"]

    with patch("e2epool.routers.checkpoint.do_finalize.delay"):
        # First finalize
        response1 = client.post(
            "/checkpoint/finalize",
            json={"checkpoint_name": checkpoint_name, "status": "success"},
            headers=AUTH_HEADER,
        )
        assert response1.status_code == 202

        # Second finalize should also return 202
        response2 = client.post(
            "/checkpoint/finalize",
            json={"checkpoint_name": checkpoint_name, "status": "success"},
            headers=AUTH_HEADER,
        )
        assert response2.status_code == 202
        assert "already" in response2.json()["detail"].lower()


def test_post_finalize_404_unknown_checkpoint(client):
    """Finalizing nonexistent checkpoint returns 404."""
    with patch("e2epool.routers.checkpoint.do_finalize.delay"):
        response = client.post(
            "/checkpoint/finalize",
            json={
                "checkpoint_name": "job-nonexistent-999-abcd1234",
                "status": "success",
            },
            headers=AUTH_HEADER,
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


def test_post_finalize_422_invalid_status(client):
    """Finalize with invalid status returns 422."""
    # Create checkpoint
    create_response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-999"},
        headers=AUTH_HEADER,
    )
    assert create_response.status_code == 201
    checkpoint_name = create_response.json()["name"]

    # Try to finalize with invalid status
    response = client.post(
        "/checkpoint/finalize",
        json={"checkpoint_name": checkpoint_name, "status": "unknown"},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 422
    assert "detail" in response.json()


def test_get_status_200(client):
    """Getting status of existing checkpoint returns 200."""
    # Create checkpoint
    create_response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-status-test"},
        headers=AUTH_HEADER,
    )
    assert create_response.status_code == 201
    checkpoint_name = create_response.json()["name"]

    # Get status
    response = client.get(
        f"/checkpoint/status/{checkpoint_name}",
        headers=AUTH_HEADER,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == checkpoint_name
    assert data["runner_id"] == "test-runner-01"
    assert data["job_id"] == "job-status-test"
    assert data["state"] == "created"


def test_get_status_404(client):
    """Getting status of nonexistent checkpoint returns 404."""
    response = client.get(
        "/checkpoint/status/job-unknown.xyz-888",
        headers=AUTH_HEADER,
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_post_create_403_token_runner_mismatch(client):
    """Using token for different runner returns 403."""
    # Try to create checkpoint for test-runner-01 using bare metal token
    response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-mismatch"},
        headers=AUTH_HEADER_BARE,
    )
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()


def test_post_create_404_runner_not_in_inventory(client):
    """Creating checkpoint for runner not in inventory returns 404."""
    response = client.post(
        "/checkpoint/create",
        json={"runner_id": "unknown-runner", "job_id": "job-unknown"},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_post_finalize_503_broker_down(client):
    """Redis/broker down returns 503 when enqueue fails."""
    # Create checkpoint first
    create_response = client.post(
        "/checkpoint/create",
        json={"runner_id": "test-runner-01", "job_id": "job-broker-test"},
        headers=AUTH_HEADER,
    )
    assert create_response.status_code == 201
    checkpoint_name = create_response.json()["name"]

    with patch(
        "e2epool.routers.checkpoint.do_finalize.delay",
        side_effect=Exception("Redis connection refused"),
    ):
        response = client.post(
            "/checkpoint/finalize",
            json={"checkpoint_name": checkpoint_name, "status": "success"},
            headers=AUTH_HEADER,
        )
        assert response.status_code == 503
        assert "broker" in response.json()["detail"].lower()
