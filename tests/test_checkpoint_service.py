import datetime
import re
from unittest.mock import patch

import pytest

from e2epool.models import Checkpoint, OperationLog
from e2epool.services.checkpoint_service import (
    CheckpointError,
    create_checkpoint,
    queue_finalize,
)


def test_create_checkpoint_success(db, mock_runner, mock_backend):
    """Create checkpoint successfully and verify DB state."""
    checkpoint = create_checkpoint(
        db, mock_runner, job_id="test-job-123", backend=mock_backend
    )

    assert checkpoint.id is not None
    assert checkpoint.name.startswith("job-")
    assert checkpoint.runner_id == mock_runner.runner_id
    assert checkpoint.job_id == "test-job-123"
    assert checkpoint.state == "created"
    assert checkpoint.created_at is not None

    # Verify checkpoint persisted in DB
    db_checkpoint = db.query(Checkpoint).filter_by(name=checkpoint.name).first()
    assert db_checkpoint is not None
    assert db_checkpoint.id == checkpoint.id

    # Verify operation log entry
    log = db.query(OperationLog).filter_by(checkpoint_id=checkpoint.id).first()
    assert log is not None
    assert log.operation == "create"
    assert log.runner_id == mock_runner.runner_id
    assert log.backend == mock_runner.backend
    assert log.result == "ok"
    assert log.started_at is not None
    assert log.finished_at is not None
    assert log.duration_ms is not None
    assert "test-job-123" in log.detail


def test_create_checkpoint_calls_backend(db, mock_runner, mock_backend):
    """Verify backend.create_checkpoint is called with correct parameters."""
    checkpoint = create_checkpoint(
        db, mock_runner, job_id="test-job-456", backend=mock_backend
    )

    mock_backend.create_checkpoint.assert_called_once()
    call_args = mock_backend.create_checkpoint.call_args
    assert call_args[0][0] == mock_runner
    assert call_args[0][1] == checkpoint.name


def test_create_checkpoint_name_format(db, mock_runner, mock_backend):
    """Verify checkpoint name matches expected pattern with hex suffix."""
    checkpoint = create_checkpoint(
        db, mock_runner, job_id="test-job-789", backend=mock_backend
    )

    # Name should match: job-{job_id}-{unix_timestamp}-{8 hex chars}
    pattern = r"^job-test-job-789-\d+-[0-9a-f]{8}$"
    assert re.match(
        pattern, checkpoint.name
    ), f"Checkpoint name '{checkpoint.name}' does not match pattern '{pattern}'"


def test_create_checkpoint_name_has_random_suffix(db, mock_runner, mock_backend):
    """Verify checkpoint name includes random hex suffix for uniqueness."""
    checkpoint = create_checkpoint(
        db, mock_runner, job_id="job-suffix", backend=mock_backend
    )

    parts = checkpoint.name.split("-")
    hex_suffix = parts[-1]
    assert len(hex_suffix) == 8
    assert all(c in "0123456789abcdef" for c in hex_suffix)


def test_create_integrity_error_returns_409(db, mock_runner, mock_backend):
    """IntegrityError from concurrent create should return 409."""
    from unittest.mock import patch as mock_patch

    from sqlalchemy.exc import IntegrityError

    # First create succeeds
    create_checkpoint(db, mock_runner, job_id="job-1", backend=mock_backend)

    # Move first checkpoint to terminal state so the app-level active check passes
    first = (
        db.query(Checkpoint)
        .filter(Checkpoint.runner_id == mock_runner.runner_id)
        .first()
    )
    first.state = "deleted"
    db.flush()

    # Mock flush to raise IntegrityError only when there are new objects to
    # persist (i.e. the checkpoint just added). Autoflush calls during queries
    # have no new objects and pass through to the real implementation.
    original_flush = type(db).flush

    def flush_side_effect(self, objects=None):
        if self.new:
            raise IntegrityError("mock", {}, Exception("unique constraint"))
        return original_flush(self, objects)

    with mock_patch.object(type(db), "flush", flush_side_effect):
        with pytest.raises(CheckpointError) as exc_info:
            create_checkpoint(db, mock_runner, job_id="job-2", backend=mock_backend)

        assert exc_info.value.status_code == 409
        assert "concurrent" in exc_info.value.detail.lower()


def test_create_checkpoint_active_exists_returns_409(db, mock_runner, mock_backend):
    """Creating a second checkpoint while first is active raises 409."""
    # Create first checkpoint
    checkpoint1 = create_checkpoint(
        db, mock_runner, job_id="job-1", backend=mock_backend
    )
    assert checkpoint1.state == "created"

    # Attempt to create second checkpoint - should fail
    with pytest.raises(CheckpointError) as exc_info:
        create_checkpoint(db, mock_runner, job_id="job-2", backend=mock_backend)

    assert exc_info.value.status_code == 409
    assert "Active checkpoint" in exc_info.value.detail
    assert checkpoint1.name in exc_info.value.detail


def test_create_checkpoint_after_terminal_state_ok(db, mock_runner, mock_backend):
    """Creating checkpoint after previous is in terminal state succeeds."""
    # Create first checkpoint
    checkpoint1 = create_checkpoint(
        db, mock_runner, job_id="job-1", backend=mock_backend
    )

    # Manually set to terminal state
    checkpoint1.state = "deleted"
    db.commit()

    # Create second checkpoint - should succeed
    checkpoint2 = create_checkpoint(
        db, mock_runner, job_id="job-2", backend=mock_backend
    )

    assert checkpoint2.id != checkpoint1.id
    assert checkpoint2.state == "created"


@patch("e2epool.services.checkpoint_service.settings")
def test_create_checkpoint_cooldown_enforced(
    mock_settings, db, mock_runner, mock_backend
):
    """Creating checkpoint during cooldown period raises 429."""
    # Set very high cooldown to ensure it always triggers
    mock_settings.finalize_cooldown_seconds = 9999

    # Create and finalize first checkpoint
    checkpoint1 = create_checkpoint(
        db, mock_runner, job_id="job-1", backend=mock_backend
    )
    checkpoint1.state = "deleted"
    checkpoint1.finalized_at = datetime.datetime.utcnow()
    db.commit()

    # Immediately attempt to create second checkpoint - should fail
    with pytest.raises(CheckpointError) as exc_info:
        create_checkpoint(db, mock_runner, job_id="job-2", backend=mock_backend)

    assert exc_info.value.status_code == 429
    assert "Cooldown period active" in exc_info.value.detail


def test_queue_finalize_success(db, mock_runner, mock_backend):
    """Queue finalize successfully and verify state transitions."""
    # Create checkpoint first
    checkpoint = create_checkpoint(
        db, mock_runner, job_id="job-1", backend=mock_backend
    )
    initial_name = checkpoint.name

    # Queue finalize
    result_checkpoint, already_finalized = queue_finalize(
        db, checkpoint.name, status="failure", source="hook"
    )

    assert already_finalized is False
    assert result_checkpoint.name == initial_name
    assert result_checkpoint.state == "finalize_queued"
    assert result_checkpoint.finalize_status == "failure"
    assert result_checkpoint.finalize_source == "hook"
    assert result_checkpoint.finalized_at is not None

    # Verify operation log
    logs = (
        db.query(OperationLog)
        .filter_by(checkpoint_id=checkpoint.id)
        .order_by(OperationLog.id)
        .all()
    )
    assert len(logs) == 2  # create + queue_finalize
    assert logs[1].operation == "queue_finalize"
    assert logs[1].runner_id == mock_runner.runner_id
    assert logs[1].result == "ok"
    assert "status=failure" in logs[1].detail
    assert "source=hook" in logs[1].detail


def test_queue_finalize_already_finalized(db, mock_runner, mock_backend):
    """Queueing finalize on already finalized checkpoint returns already=True."""
    # Create and finalize checkpoint
    checkpoint = create_checkpoint(
        db, mock_runner, job_id="job-1", backend=mock_backend
    )
    queue_finalize(db, checkpoint.name, status="success", source="hook")

    # Attempt to finalize again
    result_checkpoint, already_finalized = queue_finalize(
        db, checkpoint.name, status="failure", source="hook"
    )

    assert already_finalized is True
    assert result_checkpoint.finalize_status == "success"  # Unchanged


def test_queue_finalize_checkpoint_not_found(db):
    """Queueing finalize on non-existent checkpoint raises 404."""
    with pytest.raises(CheckpointError) as exc_info:
        queue_finalize(db, "non-existent-checkpoint", status="success")

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail


def test_queue_finalize_terminal_state_idempotent(db, mock_runner, mock_backend):
    """Queueing finalize on checkpoint in terminal state returns already=True."""
    for terminal_state in ("reset", "deleted", "gc_reset"):
        # Create checkpoint
        checkpoint = create_checkpoint(
            db, mock_runner, job_id=f"job-{terminal_state}", backend=mock_backend
        )

        # Manually set to terminal state
        checkpoint.state = terminal_state
        db.commit()

        # Finalize should be idempotent (no-op)
        result_checkpoint, already = queue_finalize(
            db, checkpoint.name, status="success"
        )
        assert already is True
        assert result_checkpoint.state == terminal_state
