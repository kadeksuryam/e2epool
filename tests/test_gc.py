"""
Tests for e2epool.tasks.gc.gc_stale_checkpoints Celery task.

All external dependencies (backends, inventory, DB sessions) are mocked.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestGcStaleCheckpoints:
    """Tests for the gc_stale_checkpoints Celery task."""

    def setup_method(self):
        """Set up common mocks for each test."""
        self.mock_session = MagicMock()

        self.mock_checkpoint_stale = MagicMock()
        self.mock_checkpoint_stale.id = 1
        self.mock_checkpoint_stale.name = "checkpoint-stale"
        self.mock_checkpoint_stale.runner_id = "runner-123"
        self.mock_checkpoint_stale.state = "created"
        self.mock_checkpoint_stale.created_at = datetime.utcnow() - timedelta(hours=25)

        self.mock_checkpoint_recent = MagicMock()
        self.mock_checkpoint_recent.id = 2
        self.mock_checkpoint_recent.name = "checkpoint-recent"
        self.mock_checkpoint_recent.runner_id = "runner-456"
        self.mock_checkpoint_recent.state = "created"
        self.mock_checkpoint_recent.created_at = datetime.utcnow() - timedelta(hours=5)

        self.mock_inventory = MagicMock()
        self.mock_runner = MagicMock()
        self.mock_runner.runner_id = "runner-123"
        self.mock_runner.backend = "proxmox"
        self.mock_runner.gitlab_runner_id = 42
        self.mock_inventory.get_runner.return_value = self.mock_runner
        self.mock_backend = MagicMock()
        self.mock_ci_adapter = MagicMock()

    def _setup_session(self, mock_create_session, checkpoints):
        mock_create_session.return_value = self.mock_session
        mock_query = self.mock_session.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_ordered = mock_filter.order_by.return_value
        mock_limit = mock_ordered.limit.return_value
        # First call returns checkpoints, second call returns [] to stop batch loop
        mock_limit.all.side_effect = [checkpoints, []]

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_resets_stale_checkpoint(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC resets stale checkpoint and sets state to gc_reset."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        gc_stale_checkpoints()

        self.mock_backend.reset.assert_called_once_with(
            self.mock_runner, self.mock_checkpoint_stale.name
        )
        self.mock_backend.check_ready.assert_called_once_with(self.mock_runner)
        assert self.mock_checkpoint_stale.state == "gc_reset"
        self.mock_session.commit.assert_called()

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_pauses_and_unpauses_runner(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC pauses runner before reset and unpauses after."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        gc_stale_checkpoints()

        self.mock_ci_adapter.pause_runner.assert_called_once_with(42)
        self.mock_ci_adapter.unpause_runner.assert_called_once_with(42)

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_acquires_and_releases_lock(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC acquires and releases advisory lock per checkpoint."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        gc_stale_checkpoints()

        mock_acquire_lock.assert_called_once_with(self.mock_session, "runner-123")
        mock_release_lock.assert_called_once_with(self.mock_session, "runner-123")

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_ignores_recent_checkpoints(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC ignores checkpoints within TTL."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend

        gc_stale_checkpoints()

        self.mock_backend.reset.assert_not_called()
        self.mock_session.query.assert_called_once()

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_ignores_finalized_checkpoints(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC only queries state='created', not finalize_queued."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend

        gc_stale_checkpoints()

        self.mock_backend.reset.assert_not_called()

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_logs_operation(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC creates OperationLog entry with operation='gc'."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        gc_stale_checkpoints()

        assert self.mock_session.add.called
        self.mock_session.commit.assert_called()

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_processes_multiple_stale_checkpoints(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC processes multiple stale checkpoints."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        mock_checkpoint_2 = MagicMock()
        mock_checkpoint_2.id = 4
        mock_checkpoint_2.name = "checkpoint-stale-2"
        mock_checkpoint_2.runner_id = "runner-999"
        mock_checkpoint_2.state = "created"
        mock_checkpoint_2.created_at = datetime.utcnow() - timedelta(hours=48)

        self._setup_session(
            mock_create_session,
            [self.mock_checkpoint_stale, mock_checkpoint_2],
        )
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        gc_stale_checkpoints()

        assert self.mock_backend.reset.call_count == 2
        assert self.mock_checkpoint_stale.state == "gc_reset"
        assert mock_checkpoint_2.state == "gc_reset"
        assert self.mock_session.add.call_count == 2

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_commits_after_processing(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC commits session after processing."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        gc_stale_checkpoints()

        self.mock_session.commit.assert_called()

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_filters_by_state_and_ttl(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC correctly filters by state='created' and created_at < TTL."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        gc_stale_checkpoints()

        self.mock_session.query.assert_called()
        mock_query = self.mock_session.query.return_value
        mock_query.filter.assert_called()

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_continues_on_backend_error(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC continues processing other checkpoints if one fails."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        mock_checkpoint_2 = MagicMock()
        mock_checkpoint_2.id = 5
        mock_checkpoint_2.name = "checkpoint-stale-3"
        mock_checkpoint_2.runner_id = "runner-888"
        mock_checkpoint_2.state = "created"
        mock_checkpoint_2.created_at = datetime.utcnow() - timedelta(hours=30)

        self._setup_session(
            mock_create_session,
            [self.mock_checkpoint_stale, mock_checkpoint_2],
        )
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        self.mock_backend.reset.side_effect = [Exception("Backend error"), None]

        gc_stale_checkpoints()

        assert self.mock_backend.reset.call_count == 2
        # First checkpoint failed, second should be gc_reset
        assert mock_checkpoint_2.state == "gc_reset"

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=False)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_skips_checkpoint_when_lock_unavailable(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """Test that GC skips a checkpoint if it can't acquire the lock."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend

        gc_stale_checkpoints()

        self.mock_backend.reset.assert_not_called()
        # State should remain unchanged
        assert self.mock_checkpoint_stale.state == "created"

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_skips_checkpoint_transitioned_after_lock(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """GC skips checkpoint if state changed to finalize_queued after lock."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        # After lock, refresh changes state to finalize_queued
        def change_state(checkpoint):
            checkpoint.state = "finalize_queued"

        self.mock_session.refresh.side_effect = lambda cp: change_state(cp)

        gc_stale_checkpoints()

        self.mock_backend.reset.assert_not_called()

    @patch("e2epool.tasks.gc.release_lock")
    @patch("e2epool.tasks.gc.acquire_lock", return_value=True)
    @patch("e2epool.tasks.gc.get_ci_adapter")
    @patch("e2epool.tasks.gc.get_backend")
    @patch("e2epool.tasks.gc.get_inventory")
    @patch("e2epool.tasks.gc.create_session")
    def test_gc_unpauses_on_reset_failure(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_backend,
        mock_get_ci_adapter,
        mock_acquire_lock,
        mock_release_lock,
    ):
        """GC unpause_runner is called even when reset raises an exception."""
        from e2epool.tasks.gc import gc_stale_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_stale])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_backend.return_value = self.mock_backend
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        self.mock_backend.reset.side_effect = Exception("Reset failed")

        gc_stale_checkpoints()

        self.mock_ci_adapter.pause_runner.assert_called_once_with(42)
        self.mock_ci_adapter.unpause_runner.assert_called_once_with(42)
