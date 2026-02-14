"""Tests for e2epool.ci_adapters.gitlab.GitLabAdapter."""

from unittest.mock import MagicMock, patch

import pytest

from e2epool.ci_adapters.gitlab import GitLabAdapter


@pytest.fixture
def adapter():
    """Create a GitLabAdapter instance for testing."""
    with patch("e2epool.ci_adapters.gitlab.settings") as mock_settings:
        mock_settings.gitlab_url = "https://gitlab.example.com"
        mock_settings.gitlab_token = "glpat-test-token"
        yield GitLabAdapter()


class TestGetJobStatus:
    """Tests for get_job_status method."""

    def test_get_job_status_running(self, adapter):
        """Test get_job_status returns 'running' for GitLab 'running' status."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "running"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-123")

            assert result == "running"
            mock_httpx.get.assert_called_once()
            call_args = mock_httpx.get.call_args
            assert call_args[0][0] == "https://gitlab.example.com/api/v4/jobs/job-123"
            assert call_args[1]["headers"] == {"PRIVATE-TOKEN": "glpat-test-token"}
            assert "timeout" in call_args[1]

    def test_get_job_status_success(self, adapter):
        """Test get_job_status returns 'success' for GitLab 'success' status."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "success"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-456")

            assert result == "success"
            mock_httpx.get.assert_called_once()

    def test_get_job_status_failed(self, adapter):
        """Test get_job_status maps GitLab 'failed' to 'failure'."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "failed"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-789")

            assert result == "failure"

    def test_get_job_status_canceled(self, adapter):
        """Test get_job_status returns 'canceled' for GitLab 'canceled' status."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "canceled"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-999")

            assert result == "canceled"

    def test_get_job_status_unknown_job_raises(self, adapter):
        """Test get_job_status raises ValueError for 404 response."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_httpx.get.return_value = mock_resp

            with pytest.raises(ValueError, match="Job .* not found"):
                adapter.get_job_status("nonexistent-job")

            mock_httpx.get.assert_called_once()

    def test_get_job_status_manual(self, adapter):
        """Test get_job_status maps 'manual' to 'running'."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "manual"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-manual")

            assert result == "running"

    def test_get_job_status_pending(self, adapter):
        """Test get_job_status maps 'pending' to 'running'."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "pending"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-pending")

            assert result == "running"

    def test_get_job_status_created(self, adapter):
        """Test get_job_status maps 'created' to 'running'."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "created"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-created")

            assert result == "running"

    def test_get_job_status_unknown_gitlab_status_defaults_to_running(self, adapter):
        """Test get_job_status defaults to 'running' for unmapped GitLab statuses."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "unknown_status"}
            mock_httpx.get.return_value = mock_resp

            result = adapter.get_job_status("job-unknown")

            assert result == "running"


class TestPauseRunner:
    """Tests for pause_runner method."""

    def test_pause_runner(self, adapter):
        """Test pause_runner sends PUT request with active=False."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_httpx.put.return_value = mock_resp

            adapter.pause_runner(42)

            mock_httpx.put.assert_called_once()
            call_args = mock_httpx.put.call_args
            assert call_args[0][0] == "https://gitlab.example.com/api/v4/runners/42"
            assert call_args[1]["headers"] == {"PRIVATE-TOKEN": "glpat-test-token"}
            assert call_args[1]["json"] == {"active": False}
            assert "timeout" in call_args[1]

    def test_pause_runner_not_found_raises(self, adapter):
        """Test pause_runner raises ValueError for 404 response."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_httpx.put.return_value = mock_resp

            with pytest.raises(ValueError, match="Runner .* not found"):
                adapter.pause_runner(999)

            mock_httpx.put.assert_called_once()


class TestUnpauseRunner:
    """Tests for unpause_runner method."""

    def test_unpause_runner(self, adapter):
        """Test unpause_runner sends PUT request with active=True."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_httpx.put.return_value = mock_resp

            adapter.unpause_runner(42)

            mock_httpx.put.assert_called_once()
            call_args = mock_httpx.put.call_args
            assert call_args[0][0] == "https://gitlab.example.com/api/v4/runners/42"
            assert call_args[1]["headers"] == {"PRIVATE-TOKEN": "glpat-test-token"}
            assert call_args[1]["json"] == {"active": True}
            assert "timeout" in call_args[1]

    def test_unpause_runner_not_found_raises(self, adapter):
        """Test unpause_runner raises ValueError for 404 response."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_httpx.put.return_value = mock_resp

            with pytest.raises(ValueError, match="Runner .* not found"):
                adapter.unpause_runner(999)

            mock_httpx.put.assert_called_once()


class TestBaseUrlHandling:
    """Tests for base_url handling."""

    def test_base_url_trailing_slash_stripped(self):
        """Test that trailing slash is stripped from base_url."""
        with patch("e2epool.ci_adapters.gitlab.settings") as mock_settings:
            mock_settings.gitlab_url = "https://gitlab.example.com/"
            mock_settings.gitlab_token = "test-token"
            adapter = GitLabAdapter()

        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "success"}
            mock_httpx.get.return_value = mock_resp

            adapter.get_job_status("job-123")

            call_args = mock_httpx.get.call_args
            called_url = call_args[0][0]
            assert called_url == "https://gitlab.example.com/api/v4/jobs/job-123"
            assert "//" not in called_url.replace("https://", "")

    def test_base_url_no_trailing_slash(self):
        """Test that base_url without trailing slash works correctly."""
        with patch("e2epool.ci_adapters.gitlab.settings") as mock_settings:
            mock_settings.gitlab_url = "https://gitlab.example.com"
            mock_settings.gitlab_token = "test-token"
            adapter = GitLabAdapter()

        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "success"}
            mock_httpx.get.return_value = mock_resp

            adapter.get_job_status("job-123")

            call_args = mock_httpx.get.call_args
            called_url = call_args[0][0]
            assert called_url == "https://gitlab.example.com/api/v4/jobs/job-123"


class TestAuthenticationHeader:
    """Tests for authentication header."""

    def test_private_token_header_included(self, adapter):
        """Test that PRIVATE-TOKEN header is included in requests."""
        with patch("e2epool.ci_adapters.gitlab.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "running"}
            mock_httpx.get.return_value = mock_resp

            adapter.get_job_status("job-123")

            call_args = mock_httpx.get.call_args
            headers = call_args[1]["headers"]
            assert headers == {"PRIVATE-TOKEN": "glpat-test-token"}
