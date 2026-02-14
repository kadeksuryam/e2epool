from unittest.mock import patch


def test_healthz_200(client):
    """Health endpoint returns 200 with status=ok when database is healthy."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["detail"] is None


def test_healthz_503_unhealthy_db_down(client, db):
    """Health endpoint returns 503 with status=unhealthy when database is down."""
    mock_context = patch.object(
        db, "execute", side_effect=Exception("Database connection failed")
    )
    with mock_context:
        response = client.get("/healthz")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert "Database connection failed" in data["detail"]
