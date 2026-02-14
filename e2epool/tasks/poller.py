import datetime

import structlog

from e2epool.config import settings
from e2epool.database import create_session
from e2epool.dependencies import get_ci_adapter, get_inventory
from e2epool.models import Checkpoint
from e2epool.services.checkpoint_service import CheckpointError, queue_finalize
from e2epool.tasks.celery_app import celery_app
from e2epool.tasks.finalize import do_finalize

logger = structlog.get_logger()


@celery_app.task(
    name="e2epool.tasks.poller.poll_active_checkpoints",
    soft_time_limit=settings.poller_soft_time_limit,
    time_limit=settings.poller_hard_time_limit,
)
def poll_active_checkpoints():
    if not settings.poller_enabled:
        return

    db = create_session()
    inventory = get_inventory()

    try:
        last_id = 0
        while True:
            batch = (
                db.query(Checkpoint)
                .filter(Checkpoint.state == "created", Checkpoint.id > last_id)
                .order_by(Checkpoint.id)
                .limit(settings.query_batch_size)
                .all()
            )
            if not batch:
                break
            last_id = batch[-1].id

            for checkpoint in batch:
                age = (
                    datetime.datetime.utcnow() - checkpoint.created_at
                ).total_seconds()
                if age < settings.poller_min_age_seconds:
                    continue

                runner = inventory.get_runner(checkpoint.runner_id)
                if not runner:
                    continue

                try:
                    ci_adapter = get_ci_adapter(runner)
                    status = ci_adapter.get_job_status(checkpoint.job_id)
                except Exception:
                    logger.exception(
                        "Failed to poll job status",
                        job_id=checkpoint.job_id,
                    )
                    continue

                if status in ("success", "failure", "canceled"):
                    try:
                        _, already = queue_finalize(
                            db, checkpoint.name, status, source="poller"
                        )
                        if not already:
                            try:
                                do_finalize.delay(checkpoint.name)
                            except Exception:
                                logger.exception(
                                    "Poller failed to enqueue finalize task",
                                    checkpoint=checkpoint.name,
                                )
                                continue
                            logger.info(
                                "Poller queued finalize",
                                checkpoint=checkpoint.name,
                                status=status,
                            )
                    except CheckpointError:
                        logger.exception(
                            "Poller failed to queue finalize",
                            checkpoint=checkpoint.name,
                        )

    finally:
        db.close()
