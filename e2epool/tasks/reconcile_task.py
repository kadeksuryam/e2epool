"""Periodic Celery task to reconcile stuck finalize_queued checkpoints."""

import structlog

from e2epool.config import settings
from e2epool.reconcile import reconcile_stuck_checkpoints
from e2epool.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    name="e2epool.tasks.reconcile_task.reconcile_stuck_finalize",
    soft_time_limit=settings.task_soft_time_limit,
    time_limit=settings.task_hard_time_limit,
)
def reconcile_stuck_finalize():
    enqueued = reconcile_stuck_checkpoints()
    if enqueued:
        logger.info("Periodic reconcile: re-enqueued stuck checkpoints", count=enqueued)
