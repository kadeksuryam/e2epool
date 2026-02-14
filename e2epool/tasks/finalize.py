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
    name="e2epool.tasks.finalize.do_finalize",
    bind=True,
    soft_time_limit=settings.task_soft_time_limit,
    time_limit=settings.task_hard_time_limit,
)
def do_finalize(self, checkpoint_name: str):
    db = create_session()
    inventory = get_inventory()
    locked = False
    runner_id = None
    paused = False
    ci_adapter = None
    gitlab_runner_id = None

    try:
        checkpoint = (
            db.query(Checkpoint).filter(Checkpoint.name == checkpoint_name).first()
        )
        if not checkpoint:
            logger.warning("Checkpoint not found", name=checkpoint_name)
            return

        if checkpoint.state != "finalize_queued":
            logger.info(
                "Checkpoint not in finalize_queued state",
                name=checkpoint_name,
                state=checkpoint.state,
            )
            return

        runner_id = checkpoint.runner_id
        runner = inventory.get_runner(runner_id)
        if not runner:
            logger.error("Runner not found in inventory", runner_id=runner_id)
            return

        locked = acquire_lock(db, runner_id)
        if not locked:
            logger.warning("Could not acquire lock", runner_id=runner_id)
            self.retry(countdown=5, max_retries=3)
            return

        # Re-verify state after acquiring lock (another worker may have processed it)
        db.expire(checkpoint)
        db.refresh(checkpoint)
        if checkpoint.state != "finalize_queued":
            logger.info(
                "Checkpoint state changed after lock acquisition",
                name=checkpoint_name,
                state=checkpoint.state,
            )
            return

        backend = get_backend(runner)
        ci_adapter = get_ci_adapter(runner)
        gitlab_runner_id = runner.gitlab_runner_id
        started = datetime.datetime.utcnow()
        result = "ok"

        try:
            # Always reset: pause -> rollback -> check ready -> unpause
            # Ensures every job starts with a clean VM state.
            if gitlab_runner_id:
                ci_adapter.pause_runner(gitlab_runner_id)
                paused = True

            try:
                backend.reset(runner, checkpoint.name)
                backend.check_ready(runner)
                checkpoint.state = "reset"
            finally:
                if paused:
                    try:
                        ci_adapter.unpause_runner(gitlab_runner_id)
                    except Exception:
                        logger.exception(
                            "Failed to unpause runner after reset",
                            runner_id=runner_id,
                        )
                    paused = False
        except Exception:
            result = "error"
            raise

        finished = datetime.datetime.utcnow()
        duration = int((finished - started).total_seconds() * 1000)

        log = OperationLog(
            checkpoint_id=checkpoint.id,
            runner_id=runner_id,
            operation="finalize",
            backend=runner.backend,
            detail=(
                f"Finalized: status={checkpoint.finalize_status}, "
                f"new_state={checkpoint.state}"
            ),
            result=result,
            started_at=started,
            finished_at=finished,
            duration_ms=duration,
        )
        db.add(log)
        db.commit()

        logger.info(
            "Finalize complete",
            checkpoint=checkpoint_name,
            state=checkpoint.state,
            duration_ms=duration,
        )

    except Exception:
        db.rollback()
        logger.exception("Finalize failed", checkpoint=checkpoint_name)
        raise
    finally:
        # Last-resort unpause guarantee
        if paused and ci_adapter and gitlab_runner_id:
            try:
                ci_adapter.unpause_runner(gitlab_runner_id)
            except Exception:
                logger.exception("Last-resort unpause failed", runner_id=runner_id)
        if locked and runner_id:
            try:
                release_lock(db, runner_id)
            except Exception:
                pass
        db.close()
