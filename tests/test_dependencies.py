"""
Tests for e2epool.dependencies — CI adapter resolution from global config.
"""

from unittest.mock import patch

import pytest

from e2epool.ci_adapters.gitlab import GitLabAdapter


class TestGetCiAdapter:
    """Tests for get_ci_adapter with global config."""

    @patch("e2epool.ci_adapters.gitlab.settings")
    @patch("e2epool.dependencies.settings")
    def test_creates_gitlab_adapter(self, mock_dep_settings, mock_gl_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_dep_settings.ci_provider = "gitlab"
        mock_gl_settings.gitlab_url = "https://gitlab.example.com"
        mock_gl_settings.gitlab_token = "glpat-xxx"

        adapter = get_ci_adapter()

        assert isinstance(adapter, GitLabAdapter)
        assert adapter._base_url == "https://gitlab.example.com"
        assert adapter._token == "glpat-xxx"

    @patch("e2epool.dependencies.settings")
    def test_unknown_provider_raises(self, mock_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_settings.ci_provider = "bitbucket"

        with pytest.raises(ValueError, match="Unknown CI provider: bitbucket"):
            get_ci_adapter()

    @patch("e2epool.ci_adapters.gitlab.settings")
    @patch("e2epool.dependencies.settings")
    def test_defaults_to_empty_strings_when_settings_none(
        self, mock_dep_settings, mock_gl_settings
    ):
        from e2epool.dependencies import get_ci_adapter

        mock_dep_settings.ci_provider = "gitlab"
        mock_gl_settings.gitlab_url = None
        mock_gl_settings.gitlab_token = None

        adapter = get_ci_adapter()

        assert isinstance(adapter, GitLabAdapter)
        assert adapter._base_url == ""
        assert adapter._token == ""
