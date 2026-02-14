import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from e2epool.models import Checkpoint, OperationLog


def test_checkpoint_table_exists(db):
    """Test that checkpoints table can be queried."""
    result = db.query(Checkpoint).all()
    assert isinstance(result, list)


def test_checkpoint_create_row(db):
    """Test inserting a checkpoint row and verifying all columns."""
    now = datetime.datetime.utcnow()
    checkpoint = Checkpoint(
        name="test-checkpoint-01",
        runner_id="runner-01",
        job_id="job-123",
        state="created",
        finalize_status=None,
        finalize_source=None,
        created_at=now,
        finalized_at=None,
    )
    db.add(checkpoint)
    db.commit()

    # Verify the row was inserted
    saved = db.query(Checkpoint).filter_by(name="test-checkpoint-01").first()
    assert saved is not None
    assert saved.name == "test-checkpoint-01"
    assert saved.runner_id == "runner-01"
    assert saved.job_id == "job-123"
    assert saved.state == "created"
    assert saved.finalize_status is None
    assert saved.finalize_source is None
    assert saved.created_at == now
    assert saved.finalized_at is None
    assert saved.id is not None


def test_checkpoint_state_check_constraint(db):
    """Test that invalid state raises IntegrityError."""
    checkpoint = Checkpoint(
        name="invalid-state-checkpoint",
        runner_id="runner-01",
        job_id="job-456",
        state="invalid_state",
    )
    db.add(checkpoint)

    with pytest.raises(IntegrityError) as exc_info:
        db.commit()

    assert "ck_checkpoint_state" in str(exc_info.value)
    db.rollback()


def test_checkpoint_status_check_constraint(db):
    """Test that invalid finalize_status raises IntegrityError."""
    checkpoint = Checkpoint(
        name="invalid-status-checkpoint",
        runner_id="runner-01",
        job_id="job-789",
        state="created",
        finalize_status="invalid_status",
    )
    db.add(checkpoint)

    with pytest.raises(IntegrityError) as exc_info:
        db.commit()

    assert "ck_checkpoint_finalize_status" in str(exc_info.value)
    db.rollback()


def test_one_active_checkpoint_per_runner(db):
    """
    Test that second 'created' checkpoint for same runner_id raises
    IntegrityError.
    """
    # Create first checkpoint with state 'created'
    checkpoint1 = Checkpoint(
        name="checkpoint-01",
        runner_id="runner-01",
        job_id="job-001",
        state="created",
    )
    db.add(checkpoint1)
    db.flush()

    # Try to create second checkpoint with state 'created' for same runner
    checkpoint2 = Checkpoint(
        name="checkpoint-02",
        runner_id="runner-01",
        job_id="job-002",
        state="created",
    )
    db.add(checkpoint2)

    with pytest.raises(IntegrityError) as exc_info:
        db.flush()

    assert "ix_one_active_checkpoint_per_runner" in str(exc_info.value)
    db.rollback()


def test_two_checkpoints_different_runners(db):
    """Test that no conflict occurs for different runner_ids."""
    checkpoint1 = Checkpoint(
        name="checkpoint-runner-01",
        runner_id="runner-01",
        job_id="job-001",
        state="created",
    )
    checkpoint2 = Checkpoint(
        name="checkpoint-runner-02",
        runner_id="runner-02",
        job_id="job-002",
        state="created",
    )

    db.add(checkpoint1)
    db.add(checkpoint2)
    db.commit()

    # Verify both were created
    saved1 = db.query(Checkpoint).filter_by(name="checkpoint-runner-01").first()
    saved2 = db.query(Checkpoint).filter_by(name="checkpoint-runner-02").first()
    assert saved1 is not None
    assert saved2 is not None
    assert saved1.runner_id == "runner-01"
    assert saved2.runner_id == "runner-02"


def test_finalize_queued_also_blocked(db):
    """Test that 'finalize_queued' and 'created' for same runner conflicts."""
    # Create first checkpoint with state 'finalize_queued'
    checkpoint1 = Checkpoint(
        name="checkpoint-finalize-queued",
        runner_id="runner-01",
        job_id="job-001",
        state="finalize_queued",
    )
    db.add(checkpoint1)
    db.flush()

    # Try to create second checkpoint with state 'created' for same runner
    checkpoint2 = Checkpoint(
        name="checkpoint-created",
        runner_id="runner-01",
        job_id="job-002",
        state="created",
    )
    db.add(checkpoint2)

    with pytest.raises(IntegrityError) as exc_info:
        db.flush()

    assert "ix_one_active_checkpoint_per_runner" in str(exc_info.value)
    db.rollback()


def test_terminal_states_dont_block(db):
    """
    Test that terminal states ('reset', 'deleted', 'gc_reset') don't block
    new 'created' checkpoints.
    """
    # Create checkpoints with terminal states
    checkpoint_reset = Checkpoint(
        name="checkpoint-reset",
        runner_id="runner-01",
        job_id="job-001",
        state="reset",
    )
    checkpoint_deleted = Checkpoint(
        name="checkpoint-deleted",
        runner_id="runner-01",
        job_id="job-002",
        state="deleted",
    )
    checkpoint_gc_reset = Checkpoint(
        name="checkpoint-gc-reset",
        runner_id="runner-01",
        job_id="job-003",
        state="gc_reset",
    )

    db.add(checkpoint_reset)
    db.add(checkpoint_deleted)
    db.add(checkpoint_gc_reset)
    db.flush()

    # Should be able to create a new 'created' checkpoint for same runner
    checkpoint_new = Checkpoint(
        name="checkpoint-new-created",
        runner_id="runner-01",
        job_id="job-004",
        state="created",
    )
    db.add(checkpoint_new)
    db.commit()

    # Verify all were created
    saved = db.query(Checkpoint).filter_by(runner_id="runner-01").all()
    assert len(saved) == 4
    states = {c.state for c in saved}
    assert states == {"reset", "deleted", "gc_reset", "created"}


def test_operation_log_table_exists(db):
    """Test that operation_logs table can be queried."""
    result = db.query(OperationLog).all()
    assert isinstance(result, list)


def test_operation_log_fk_checkpoint(db):
    """Test that foreign key constraint works for checkpoint_id."""
    # Try to create operation log with non-existent checkpoint_id
    operation_log = OperationLog(
        checkpoint_id=99999,
        runner_id="runner-01",
        operation="test_operation",
        detail="test detail",
    )
    db.add(operation_log)

    with pytest.raises(IntegrityError) as exc_info:
        db.commit()

    # FK violation should mention the foreign key or checkpoints
    error_msg = str(exc_info.value).lower()
    assert "foreign key" in error_msg or "checkpoints" in error_msg
    db.rollback()


def test_operation_log_insert(db):
    """Test inserting an operation log and verifying columns."""
    # First create a checkpoint
    checkpoint = Checkpoint(
        name="checkpoint-for-log",
        runner_id="runner-01",
        job_id="job-123",
        state="created",
    )
    db.add(checkpoint)
    db.commit()

    # Create operation log
    now = datetime.datetime.utcnow()
    operation_log = OperationLog(
        checkpoint_id=checkpoint.id,
        runner_id="runner-01",
        operation="create",
        backend="proxmox",
        detail="Created checkpoint successfully",
        result="ok",
        started_at=now,
        finished_at=now,
        duration_ms=150,
    )
    db.add(operation_log)
    db.commit()

    # Verify the row was inserted
    saved = db.query(OperationLog).filter_by(checkpoint_id=checkpoint.id).first()
    assert saved is not None
    assert saved.checkpoint_id == checkpoint.id
    assert saved.runner_id == "runner-01"
    assert saved.operation == "create"
    assert saved.backend == "proxmox"
    assert saved.detail == "Created checkpoint successfully"
    assert saved.result == "ok"
    assert saved.started_at == now
    assert saved.finished_at == now
    assert saved.duration_ms == 150
    assert saved.id is not None

    # Verify relationship
    assert saved.checkpoint.name == "checkpoint-for-log"
