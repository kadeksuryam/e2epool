import datetime

import structlog

from e2epool.config import settings
from e2epool.database import create_session
from e2epool.dependencies import get_backend, get_ci_adapter, get_inventory
from e2epool.locking import acquire_lock, release_lock
from e2epool.models import Checkpoint, OperationLog
from e2epool.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    name="e2epool.tasks.gc.gc_stale_checkpoints",
    soft_time_limit=settings.task_soft_time_limit,
    time_limit=settings.task_hard_time_limit,
)
def gc_stale_checkpoints():
    db = create_session()
    inventory = get_inventory()

    try:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(
            seconds=settings.checkpoint_ttl_seconds
        )

        last_id = 0
        while True:
            batch = (
                db.query(Checkpoint)
                .filter(
                    Checkpoint.state == "created",
                    Checkpoint.created_at < cutoff,
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
                runner = inventory.get_runner(checkpoint.runner_id)
                if not runner:
                    logger.warning(
                        "GC: runner not in inventory",
                        runner_id=checkpoint.runner_id,
                    )
                    continue

                locked = False
                paused = False
                ci_adapter = None
                gitlab_runner_id = None
                try:
                    locked = acquire_lock(db, checkpoint.runner_id)
                    if not locked:
                        logger.warning(
                            "GC: could not acquire lock, skipping",
                            runner_id=checkpoint.runner_id,
                        )
                        continue

                    # Re-verify state after lock
                    db.expire(checkpoint)
                    db.refresh(checkpoint)
                    if checkpoint.state != "created":
                        logger.info(
                            "GC: checkpoint state changed after lock",
                            checkpoint=checkpoint.name,
                            state=checkpoint.state,
                        )
                        continue

                    backend = get_backend(runner)
                    ci_adapter = get_ci_adapter(runner)
                    gitlab_runner_id = runner.gitlab_runner_id
                    started = datetime.datetime.utcnow()
                    result = "ok"

                    try:
                        if gitlab_runner_id:
                            ci_adapter.pause_runner(gitlab_runner_id)
                            paused = True

                        try:
                            backend.reset(runner, checkpoint.name)
                            backend.check_ready(runner)
                        finally:
                            if paused:
                                try:
                                    ci_adapter.unpause_runner(gitlab_runner_id)
                                except Exception:
                                    logger.exception(
                                        "GC: failed to unpause runner",
                                        runner_id=checkpoint.runner_id,
                                    )
                                paused = False
                    except Exception:
                        result = "error"
                        raise

                    finished = datetime.datetime.utcnow()
                    duration = int((finished - started).total_seconds() * 1000)

                    checkpoint.state = "gc_reset"

                    log = OperationLog(
                        checkpoint_id=checkpoint.id,
                        runner_id=checkpoint.runner_id,
                        operation="gc",
                        backend=runner.backend,
                        detail="Stale checkpoint reset by GC",
                        result=result,
                        started_at=started,
                        finished_at=finished,
                        duration_ms=duration,
                    )
                    db.add(log)
                    db.commit()

                    logger.info(
                        "GC reset checkpoint",
                        checkpoint=checkpoint.name,
                        duration_ms=duration,
                    )
                except Exception:
                    db.rollback()
                    logger.exception(
                        "GC failed for checkpoint",
                        checkpoint=checkpoint.name,
                    )
                finally:
                    # Last-resort unpause guarantee
                    if paused and ci_adapter and gitlab_runner_id:
                        try:
                            ci_adapter.unpause_runner(gitlab_runner_id)
                        except Exception:
                            logger.exception(
                                "GC: last-resort unpause failed",
                                runner_id=checkpoint.runner_id,
                            )
                    if locked:
                        try:
                            release_lock(db, checkpoint.runner_id)
                        except Exception:
                            pass

    finally:
        db.close()
