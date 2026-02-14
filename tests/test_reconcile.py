"""Tests for e2epool.reconcile reconciliation functions."""

from unittest.mock import MagicMock, patch


class TestReconcileStuckCheckpoints:
    """Tests for the reconcile_stuck_checkpoints function."""

    @patch("e2epool.tasks.finalize.do_finalize")
    @patch("e2epool.reconcile.create_session")
    def test_reconcile_reenqueues_stuck_checkpoints(
        self, mock_create_session, mock_do_finalize
    ):
        """Stuck finalize_queued checkpoints are re-enqueued."""
        from e2epool.reconcile import reconcile_stuck_checkpoints

        mock_session = MagicMock()
        mock_create_session.return_value = mock_session

        stuck_1 = MagicMock()
        stuck_1.name = "job-1-111"
        stuck_1.runner_id = "runner-01"
        stuck_1.finalize_status = "failure"

        stuck_2 = MagicMock()
        stuck_2.name = "job-2-222"
        stuck_2.runner_id = "runner-02"
        stuck_2.finalize_status = "success"

        stuck = [stuck_1, stuck_2]
        mock_filter = mock_session.query.return_value.filter.return_value
        mock_ordered = mock_filter.order_by.return_value
        mock_limit = mock_ordered.limit.return_value
        mock_limit.all.side_effect = [stuck, []]

        result = reconcile_stuck_checkpoints()

        assert result == 2
        assert mock_do_finalize.delay.call_count == 2
        mock_do_finalize.delay.assert_any_call("job-1-111")
        mock_do_finalize.delay.assert_any_call("job-2-222")
        mock_session.close.assert_called_once()

    @patch("e2epool.tasks.finalize.do_finalize")
    @patch("e2epool.reconcile.create_session")
    def test_reconcile_no_stuck_checkpoints(
        self, mock_create_session, mock_do_finalize
    ):
        """No-op when no stuck checkpoints exist."""
        from e2epool.reconcile import reconcile_stuck_checkpoints

        mock_session = MagicMock()
        mock_create_session.return_value = mock_session
        mock_filter = mock_session.query.return_value.filter.return_value
        mock_ordered = mock_filter.order_by.return_value
        mock_limit = mock_ordered.limit.return_value
        mock_limit.all.return_value = []

        result = reconcile_stuck_checkpoints()

        assert result == 0
        mock_do_finalize.delay.assert_not_called()
        mock_session.close.assert_called_once()

    @patch("e2epool.tasks.finalize.do_finalize")
    @patch("e2epool.reconcile.create_session")
    def test_reconcile_closes_session_on_error(
        self, mock_create_session, mock_do_finalize
    ):
        """Session is closed even if an error occurs."""
        from e2epool.reconcile import reconcile_stuck_checkpoints

        mock_session = MagicMock()
        mock_create_session.return_value = mock_session
        mock_session.query.side_effect = Exception("DB error")

        try:
            reconcile_stuck_checkpoints()
        except Exception:
            pass

        mock_session.close.assert_called_once()


class TestReconcileOnStartup:
    """Tests for the reconcile_on_startup wrapper."""

    @patch("e2epool.reconcile.reconcile_stuck_checkpoints", return_value=3)
    def test_reconcile_on_startup_delegates(self, mock_reconcile):
        """reconcile_on_startup delegates to reconcile_stuck_checkpoints."""
        from e2epool.reconcile import reconcile_on_startup

        reconcile_on_startup()

        mock_reconcile.assert_called_once()


class TestReconcileStuckFinalizeTask:
    """Tests for the periodic Celery task."""

    @patch("e2epool.tasks.reconcile_task.reconcile_stuck_checkpoints", return_value=2)
    def test_periodic_task_calls_reconcile(self, mock_reconcile):
        """Periodic task delegates to reconcile_stuck_checkpoints."""
        from e2epool.tasks.reconcile_task import reconcile_stuck_finalize

        reconcile_stuck_finalize()

        mock_reconcile.assert_called_once()
