"""
Tests for e2epool.tasks.poller.poll_active_checkpoints Celery task.

All external dependencies (CI adapters, inventory, DB sessions) are mocked.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestPollActiveCheckpoints:
    """Tests for the poll_active_checkpoints Celery task."""

    def setup_method(self):
        """Set up common mocks for each test."""
        self.mock_session = MagicMock()

        self.mock_checkpoint_aged = MagicMock()
        self.mock_checkpoint_aged.id = 1
        self.mock_checkpoint_aged.name = "checkpoint-aged"
        self.mock_checkpoint_aged.runner_id = "runner-123"
        self.mock_checkpoint_aged.state = "created"
        self.mock_checkpoint_aged.job_id = "job-aged"
        self.mock_checkpoint_aged.created_at = datetime.utcnow() - timedelta(minutes=5)

        self.mock_checkpoint_recent = MagicMock()
        self.mock_checkpoint_recent.id = 2
        self.mock_checkpoint_recent.name = "checkpoint-recent"
        self.mock_checkpoint_recent.runner_id = "runner-456"
        self.mock_checkpoint_recent.state = "created"
        self.mock_checkpoint_recent.job_id = "job-recent"
        self.mock_checkpoint_recent.created_at = datetime.utcnow() - timedelta(
            seconds=30
        )

        self.mock_inventory = MagicMock()
        self.mock_ci_adapter = MagicMock()

    def _setup_session(self, mock_create_session, checkpoints):
        mock_create_session.return_value = self.mock_session
        mock_query = self.mock_session.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_ordered = mock_filter.order_by.return_value
        mock_limit = mock_ordered.limit.return_value
        # First call returns checkpoints, second call returns [] to stop batch loop
        mock_limit.all.side_effect = [checkpoints, []]

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_detects_completed_job(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Test that poller detects completed job and triggers finalization."""
        from e2epool.tasks.poller import poll_active_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_aged])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter
        self.mock_ci_adapter.get_job_status.return_value = "success"
        mock_queue_finalize.return_value = (self.mock_checkpoint_aged, False)

        poll_active_checkpoints()

        self.mock_ci_adapter.get_job_status.assert_called_once_with("job-aged")
        mock_queue_finalize.assert_called_once_with(
            self.mock_session, "checkpoint-aged", "success", source="poller"
        )
        mock_do_finalize.delay.assert_called_once_with("checkpoint-aged")

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_ignores_running_jobs(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Test that poller ignores jobs still running."""
        from e2epool.tasks.poller import poll_active_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_aged])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter
        self.mock_ci_adapter.get_job_status.return_value = "running"

        poll_active_checkpoints()

        self.mock_ci_adapter.get_job_status.assert_called_once_with("job-aged")
        mock_queue_finalize.assert_not_called()
        mock_do_finalize.delay.assert_not_called()

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_skips_recent_checkpoints(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Test that poller skips checkpoints younger than poller_min_age_seconds."""
        from e2epool.tasks.poller import poll_active_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_recent])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        poll_active_checkpoints()

        self.mock_ci_adapter.get_job_status.assert_not_called()
        mock_queue_finalize.assert_not_called()
        mock_do_finalize.delay.assert_not_called()

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_sets_finalize_source_poller(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Test that queue_finalize is called with source='poller'."""
        from e2epool.tasks.poller import poll_active_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_aged])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter
        self.mock_ci_adapter.get_job_status.return_value = "failure"
        mock_queue_finalize.return_value = (self.mock_checkpoint_aged, False)

        poll_active_checkpoints()

        mock_queue_finalize.assert_called_once_with(
            self.mock_session, "checkpoint-aged", "failure", source="poller"
        )

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_handles_canceled_status(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Test that poller handles canceled job status."""
        from e2epool.tasks.poller import poll_active_checkpoints

        self._setup_session(mock_create_session, [self.mock_checkpoint_aged])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter
        self.mock_ci_adapter.get_job_status.return_value = "canceled"
        mock_queue_finalize.return_value = (self.mock_checkpoint_aged, False)

        poll_active_checkpoints()

        mock_queue_finalize.assert_called_once_with(
            self.mock_session, "checkpoint-aged", "canceled", source="poller"
        )
        mock_do_finalize.delay.assert_called_once_with("checkpoint-aged")

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_processes_multiple_checkpoints(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Test that poller processes multiple aged checkpoints."""
        from e2epool.tasks.poller import poll_active_checkpoints

        mock_checkpoint_2 = MagicMock()
        mock_checkpoint_2.id = 3
        mock_checkpoint_2.name = "checkpoint-2"
        mock_checkpoint_2.runner_id = "runner-789"
        mock_checkpoint_2.state = "created"
        mock_checkpoint_2.job_id = "job-2"
        mock_checkpoint_2.created_at = datetime.utcnow() - timedelta(minutes=10)

        self._setup_session(
            mock_create_session, [self.mock_checkpoint_aged, mock_checkpoint_2]
        )
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter
        self.mock_ci_adapter.get_job_status.side_effect = ["success", "failure"]
        mock_queue_finalize.return_value = (MagicMock(), False)

        poll_active_checkpoints()

        assert self.mock_ci_adapter.get_job_status.call_count == 2
        assert mock_queue_finalize.call_count == 2
        assert mock_do_finalize.delay.call_count == 2

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_only_queries_created_state(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Test that poller only queries checkpoints with state='created'."""
        from e2epool.tasks.poller import poll_active_checkpoints

        self._setup_session(mock_create_session, [])
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter

        poll_active_checkpoints()

        self.mock_session.query.assert_called()
        self.mock_ci_adapter.get_job_status.assert_not_called()

    @patch("e2epool.tasks.poller.do_finalize")
    @patch("e2epool.tasks.poller.queue_finalize")
    @patch("e2epool.tasks.poller.get_ci_adapter")
    @patch("e2epool.tasks.poller.get_inventory")
    @patch("e2epool.tasks.poller.create_session")
    def test_poller_continues_on_enqueue_failure(
        self,
        mock_create_session,
        mock_get_inventory,
        mock_get_ci_adapter,
        mock_queue_finalize,
        mock_do_finalize,
    ):
        """Broker error on do_finalize.delay doesn't crash the poller."""
        from e2epool.tasks.poller import poll_active_checkpoints

        mock_checkpoint_2 = MagicMock()
        mock_checkpoint_2.id = 3
        mock_checkpoint_2.name = "checkpoint-2"
        mock_checkpoint_2.runner_id = "runner-789"
        mock_checkpoint_2.state = "created"
        mock_checkpoint_2.job_id = "job-2"
        mock_checkpoint_2.created_at = datetime.utcnow() - timedelta(minutes=10)

        self._setup_session(
            mock_create_session,
            [self.mock_checkpoint_aged, mock_checkpoint_2],
        )
        mock_get_inventory.return_value = self.mock_inventory
        mock_get_ci_adapter.return_value = self.mock_ci_adapter
        self.mock_ci_adapter.get_job_status.side_effect = ["success", "failure"]
        mock_queue_finalize.return_value = (MagicMock(), False)

        # First delay fails, second succeeds
        mock_do_finalize.delay.side_effect = [
            Exception("Redis connection refused"),
            None,
        ]

        # Should not crash
        poll_active_checkpoints()

        assert mock_do_finalize.delay.call_count == 2
