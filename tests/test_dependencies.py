"""
Tests for e2epool.dependencies — CI adapter resolution from global config.
"""

from unittest.mock import patch

from e2epool.ci_adapters.gitlab import GitLabAdapter


class TestGetCiAdapter:
    """Tests for get_ci_adapter with global config."""

    @patch("e2epool.dependencies.settings")
    def test_creates_adapter_from_global_config(self, mock_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_settings.ci_provider = "gitlab"
        mock_settings.ci_url = "https://gitlab.example.com"
        mock_settings.ci_token = "glpat-xxx"

        adapter = get_ci_adapter()

        assert isinstance(adapter, GitLabAdapter)
        assert adapter._base_url == "https://gitlab.example.com"
        assert adapter._token == "glpat-xxx"

    @patch("e2epool.dependencies.settings")
    def test_unknown_provider_raises(self, mock_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_settings.ci_provider = "bitbucket"

        import pytest

        with pytest.raises(ValueError, match="Unknown CI provider: bitbucket"):
            get_ci_adapter()

    @patch("e2epool.dependencies.settings")
    def test_defaults_to_empty_strings_when_url_token_none(self, mock_settings):
        from e2epool.dependencies import get_ci_adapter

        mock_settings.ci_provider = "gitlab"
        mock_settings.ci_url = None
        mock_settings.ci_token = None

        adapter = get_ci_adapter()

        assert isinstance(adapter, GitLabAdapter)
        assert adapter._base_url == ""
        assert adapter._token == ""
