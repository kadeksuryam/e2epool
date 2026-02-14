"""
Tests for e2epool.dependencies — CI adapter resolution with global config.
"""

from unittest.mock import patch

from e2epool.ci_adapters.gitlab import GitLabAdapter
from e2epool.inventory import RunnerConfig


class TestGetCiAdapter:
    """Tests for get_ci_adapter with global vs per-runner config."""

    def _make_runner(self, **kwargs):
        defaults = {
            "runner_id": "test-runner",
            "backend": "proxmox",
            "token": "secret",
            "ci_adapter": "gitlab",
            "gitlab_url": "https://runner-gitlab.example.com",
            "gitlab_token": "glpat-runner",
        }
        defaults.update(kwargs)
        return RunnerConfig(**defaults)

    @patch("e2epool.dependencies.settings")
    def test_global_config_takes_priority(self, mock_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_settings.gitlab_url = "https://global-gitlab.example.com"
        mock_settings.gitlab_token = "glpat-global"

        runner = self._make_runner()
        adapter = get_ci_adapter(runner)

        assert isinstance(adapter, GitLabAdapter)
        assert adapter._base_url == "https://global-gitlab.example.com"
        assert adapter._token == "glpat-global"

    @patch("e2epool.dependencies.settings")
    def test_falls_back_to_runner_config(self, mock_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_settings.gitlab_url = None
        mock_settings.gitlab_token = None

        runner = self._make_runner(
            gitlab_url="https://runner-gitlab.example.com",
            gitlab_token="glpat-runner",
            gitlab_project_id=42,
        )
        adapter = get_ci_adapter(runner)

        assert isinstance(adapter, GitLabAdapter)
        assert adapter._base_url == "https://runner-gitlab.example.com"
        assert adapter._token == "glpat-runner"
        assert adapter._project_id == 42

    @patch("e2epool.dependencies.settings")
    def test_global_config_no_project_id(self, mock_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_settings.gitlab_url = "https://global-gitlab.example.com"
        mock_settings.gitlab_token = "glpat-global"

        runner = self._make_runner(gitlab_project_id=99)
        adapter = get_ci_adapter(runner)

        assert isinstance(adapter, GitLabAdapter)
        # Global config does not pass project_id
        assert adapter._project_id is None

    @patch("e2epool.dependencies.settings")
    def test_partial_global_config_falls_back(self, mock_settings):
        """Only URL set globally, no token — falls back to per-runner."""
        from e2epool.dependencies import get_ci_adapter

        mock_settings.gitlab_url = "https://global-gitlab.example.com"
        mock_settings.gitlab_token = None

        runner = self._make_runner(
            gitlab_url="https://runner-gitlab.example.com",
            gitlab_token="glpat-runner",
        )
        adapter = get_ci_adapter(runner)

        assert isinstance(adapter, GitLabAdapter)
        assert adapter._base_url == "https://runner-gitlab.example.com"
        assert adapter._token == "glpat-runner"
