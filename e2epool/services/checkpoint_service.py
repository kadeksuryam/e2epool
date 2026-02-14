import datetime
import os
import time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from e2epool.backends.base import BackendProtocol
from e2epool.config import settings
from e2epool.inventory import RunnerConfig
from e2epool.models import ACTIVE_STATES, TERMINAL_STATES, Checkpoint, OperationLog


class CheckpointError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def create_checkpoint(
    db: Session,
    runner: RunnerConfig,
    job_id: str,
    backend: BackendProtocol,
    caller: str | None = None,
) -> Checkpoint:
    # Check cooldown: most recent finalized checkpoint for this runner
    recent = (
        db.query(Checkpoint)
        .filter(
            Checkpoint.runner_id == runner.runner_id,
            Checkpoint.finalized_at.isnot(None),
        )
        .order_by(Checkpoint.finalized_at.desc())
        .first()
    )
    if recent and recent.finalized_at:
        elapsed = (datetime.datetime.utcnow() - recent.finalized_at).total_seconds()
        if elapsed < settings.finalize_cooldown_seconds:
            raise CheckpointError(429, "Cooldown period active, try again later")

    # Check for existing active checkpoint (FOR UPDATE serializes concurrent creates)
    active = (
        db.query(Checkpoint)
        .filter(
            Checkpoint.runner_id == runner.runner_id,
            Checkpoint.state.in_(ACTIVE_STATES),
        )
        .with_for_update()
        .first()
    )
    if active:
        raise CheckpointError(
            409,
            f"Active checkpoint '{active.name}' already exists for runner "
            f"'{runner.runner_id}'",
        )

    name = f"job-{job_id}-{int(time.time())}-{os.urandom(4).hex()}"

    started = datetime.datetime.utcnow()
    backend.create_checkpoint(runner, name)
    finished = datetime.datetime.utcnow()
    duration = int((finished - started).total_seconds() * 1000)

    checkpoint = Checkpoint(
        name=name,
        runner_id=runner.runner_id,
        job_id=job_id,
        state="created",
    )
    db.add(checkpoint)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise CheckpointError(
            409,
            f"Active checkpoint already exists for runner '{runner.runner_id}' "
            "(concurrent create)",
        )

    detail = f"Checkpoint created for job {job_id}"
    if caller:
        detail += f", caller={caller}"

    log = OperationLog(
        checkpoint_id=checkpoint.id,
        runner_id=runner.runner_id,
        operation="create",
        backend=runner.backend,
        detail=detail,
        result="ok",
        started_at=started,
        finished_at=finished,
        duration_ms=duration,
    )
    db.add(log)
    db.commit()
    db.refresh(checkpoint)
    return checkpoint


def queue_finalize(
    db: Session,
    checkpoint_name: str,
    status: str,
    source: str = "hook",
) -> tuple[Checkpoint, bool]:
    """Queue a checkpoint for finalization.

    Returns (checkpoint, already_finalized).
    """
    checkpoint = db.query(Checkpoint).filter(Checkpoint.name == checkpoint_name).first()
    if not checkpoint:
        raise CheckpointError(404, f"Checkpoint '{checkpoint_name}' not found")

    # Idempotent: already queued or in terminal state
    if checkpoint.state == "finalize_queued":
        return checkpoint, True
    if checkpoint.state in TERMINAL_STATES:
        return checkpoint, True

    if checkpoint.state != "created":
        raise CheckpointError(
            409,
            f"Checkpoint '{checkpoint_name}' in state '{checkpoint.state}', "
            "cannot finalize",
        )

    now = datetime.datetime.utcnow()
    checkpoint.state = "finalize_queued"
    checkpoint.finalize_status = status
    checkpoint.finalize_source = source
    checkpoint.finalized_at = now

    log = OperationLog(
        checkpoint_id=checkpoint.id,
        runner_id=checkpoint.runner_id,
        operation="queue_finalize",
        detail=f"Finalize queued: status={status}, source={source}",
        result="ok",
        started_at=now,
        finished_at=now,
        duration_ms=0,
    )
    db.add(log)
    db.commit()
    db.refresh(checkpoint)
    return checkpoint, False


def get_checkpoint_by_name(db: Session, name: str) -> Checkpoint | None:
    return db.query(Checkpoint).filter(Checkpoint.name == name).first()


def get_active_checkpoint_for_runner(db: Session, runner_id: str) -> Checkpoint | None:
    return (
        db.query(Checkpoint)
        .filter(
            Checkpoint.runner_id == runner_id,
            Checkpoint.state.in_(ACTIVE_STATES),
        )
        .first()
    )
