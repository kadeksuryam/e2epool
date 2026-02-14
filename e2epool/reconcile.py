"""Reconciliation: re-enqueue stuck finalize_queued checkpoints."""

import structlog

from e2epool.config import settings
from e2epool.database import create_session
from e2epool.models import Checkpoint

logger = structlog.get_logger()


def reconcile_stuck_checkpoints() -> int:
    """Scan for checkpoints stuck in finalize_queued and re-enqueue them.

    Returns the number of checkpoints re-enqueued.
    """
    # Import here to avoid circular import with celery_app
    from e2epool.tasks.finalize import do_finalize

    db = create_session()
    try:
        enqueued = 0
        last_id = 0
        while True:
            batch = (
                db.query(Checkpoint)
                .filter(
                    Checkpoint.state == "finalize_queued",
                    Checkpoint.id > last_id,
                )
                .order_by(Checkpoint.id)
                .limit(settings.query_batch_size)
                .all()
            )
            if not batch:
                break
            last_id = batch[-1].id

            for checkpoint in batch:
                logger.info(
                    "Reconcile: re-enqueuing stuck checkpoint",
                    checkpoint=checkpoint.name,
                    runner_id=checkpoint.runner_id,
                    finalize_status=checkpoint.finalize_status,
                )
                try:
                    do_finalize.delay(checkpoint.name)
                    enqueued += 1
                except Exception:
                    logger.exception(
                        "Reconcile: failed to enqueue checkpoint",
                        checkpoint=checkpoint.name,
                    )

        return enqueued
    finally:
        db.close()


def reconcile_on_startup():
    """Run reconciliation once at controller startup."""
    enqueued = reconcile_stuck_checkpoints()
    if enqueued:
        logger.info("Reconcile: re-enqueued stuck checkpoints", count=enqueued)
    else:
        logger.info("Reconcile: no stuck checkpoints found")
