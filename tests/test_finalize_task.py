"""
Tests for e2epool.tasks.finalize.do_finalize Celery task.

All external dependencies (backends, CI adapters, inventory, locking, DB
sessions) are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestDoFinalize:
    """Tests for the do_finalize Celery task."""

    def setup_method(self):
        """Set up common mocks for each test."""
        self.mock_checkpoint = MagicMock()
        self.mock_checkpoint.id = 1
        self.mock_checkpoint.name = "test-checkpoint"
        self.mock_checkpoint.runner_id = "runner-123"
        self.mock_checkpoint.state = "finalize_queued"
        self.mock_checkpoint.finalize_status = None
        self.mock_checkpoint.job_id = "job-456"

        self.mock_runner = MagicMock()
        self.mock_runner.runner_id = "runner-123"
        self.mock_runner.cleanup_cmd = None
        self.mock_runner.gitlab_runner_id = 42

        self.mock_session = MagicMock()
        self.mock_query = self.mock_session.query.return_value
        self.mock_filter = self.mock_query.filter.return_value
        self.mock_filter.first.return_value = self.mock_checkpoint

        self.mock_inventory = MagicMock()
        self.mock_inventory.get_runner.return_value = self.mock_runner

        self.mock_backend = MagicMock()
        self.mock_ci_adapter = MagicMock()

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_failure_resets_and_checks_readiness(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that failure status triggers backend reset and readiness check."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "failure"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        self.mock_backend.reset.assert_called_once_with(
            self.mock_runner, self.mock_checkpoint.name
        )
        self.mock_backend.check_ready.assert_called_once_with(self.mock_runner)
        assert self.mock_checkpoint.state == "reset"
        self.mock_session.commit.assert_called()

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_failure_pauses_and_unpauses_runner(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that failure status pauses and unpauses the runner."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "failure"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        self.mock_ci_adapter.pause_runner.assert_called_once_with(42)
        self.mock_ci_adapter.unpause_runner.assert_called_once_with(42)

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_success_deletes_checkpoint(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that success status without cleanup_cmd deletes checkpoint."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "success"
        self.mock_runner.cleanup_cmd = None
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        self.mock_backend.cleanup.assert_called_once_with(
            self.mock_runner, self.mock_checkpoint.name
        )
        assert self.mock_checkpoint.state == "deleted"
        self.mock_session.commit.assert_called()

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_success_with_cleanup_pauses(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that success with cleanup_cmd pauses and unpauses runner."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "success"
        self.mock_runner.cleanup_cmd = "cleanup.sh"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        self.mock_ci_adapter.pause_runner.assert_called_once_with(42)
        self.mock_backend.cleanup.assert_called_once_with(
            self.mock_runner, self.mock_checkpoint.name
        )
        self.mock_ci_adapter.unpause_runner.assert_called_once_with(42)
        assert self.mock_checkpoint.state == "deleted"

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_success_no_cleanup_no_pause(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that success without cleanup_cmd does not pause runner."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "success"
        self.mock_runner.cleanup_cmd = None
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        self.mock_ci_adapter.pause_runner.assert_not_called()
        self.mock_ci_adapter.unpause_runner.assert_not_called()
        self.mock_backend.cleanup.assert_called_once()

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_acquires_and_releases_lock(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that advisory lock is acquired and released."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "success"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        mock_acquire_lock.assert_called_once_with(self.mock_session, "runner-123")
        mock_release_lock.assert_called_once_with(self.mock_session, "runner-123")

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_lock_released_on_exception(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that lock is released even when backend.reset raises exception."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "failure"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        self.mock_backend.reset.side_effect = Exception("Backend error")

        with pytest.raises(Exception, match="Backend error"):
            do_finalize("test-checkpoint")

        mock_release_lock.assert_called_once_with(self.mock_session, "runner-123")

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_logs_operation(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that OperationLog entry is created."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "success"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        assert self.mock_session.add.called
        self.mock_session.commit.assert_called()

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_canceled_resets_checkpoint(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that canceled status also triggers reset flow."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "canceled"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        do_finalize("test-checkpoint")

        self.mock_backend.reset.assert_called_once_with(
            self.mock_runner, self.mock_checkpoint.name
        )
        self.mock_backend.check_ready.assert_called_once_with(self.mock_runner)
        assert self.mock_checkpoint.state == "reset"
        self.mock_ci_adapter.pause_runner.assert_called_once_with(42)
        self.mock_ci_adapter.unpause_runner.assert_called_once_with(42)

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_re_verifies_state_after_lock(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """State changed between read and lock acquisition -> early return."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "failure"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        # After lock acquired, refresh shows state changed to "reset"
        def change_state(checkpoint):
            checkpoint.state = "reset"

        self.mock_session.refresh.side_effect = lambda cp: change_state(cp)

        do_finalize("test-checkpoint")

        # Backend should NOT have been called since state is no longer
        # finalize_queued
        self.mock_backend.reset.assert_not_called()
        self.mock_backend.cleanup.assert_not_called()

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_unpauses_on_reset_failure(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """unpause_runner is called even when reset raises an exception."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "failure"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        self.mock_backend.reset.side_effect = Exception("Reset failed")

        with pytest.raises(Exception, match="Reset failed"):
            do_finalize("test-checkpoint")

        self.mock_ci_adapter.pause_runner.assert_called_once_with(42)
        self.mock_ci_adapter.unpause_runner.assert_called_once_with(42)

    @patch("e2epool.tasks.finalize.release_lock")
    @patch("e2epool.tasks.finalize.acquire_lock", return_value=True)
    @patch("e2epool.tasks.finalize.get_ci_adapter")
    @patch("e2epool.tasks.finalize.get_backend")
    @patch("e2epool.tasks.finalize.get_inventory")
    @patch("e2epool.tasks.finalize.create_session")
    def test_finalize_unpauses_on_cleanup_failure(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """unpause_runner is called even when cleanup raises an exception."""
        from e2epool.tasks.finalize import do_finalize

        self.mock_checkpoint.finalize_status = "success"
        self.mock_runner.cleanup_cmd = "cleanup.sh"
        mock_create_session.return_value = self.mock_session
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        self.mock_backend.cleanup.side_effect = Exception("Cleanup failed")

        with pytest.raises(Exception, match="Cleanup failed"):
            do_finalize("test-checkpoint")

        self.mock_ci_adapter.pause_runner.assert_called_once_with(42)
        self.mock_ci_adapter.unpause_runner.assert_called_once_with(42)
