from celery import Celery

from e2epool.config import settings

celery_app = Celery("e2epool", broker=settings.redis_url)

celery_app.conf.update(
    result_backend=settings.redis_url,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "poll-ci-jobs": {
            "task": "e2epool.tasks.poller.poll_active_checkpoints",
            "schedule": settings.poller_interval_seconds,
        },
        "gc-stale-checkpoints": {
            "task": "e2epool.tasks.gc.gc_stale_checkpoints",
            "schedule": settings.gc_interval_seconds,
        },
        "reconcile-stuck-finalize": {
            "task": "e2epool.tasks.reconcile_task.reconcile_stuck_finalize",
            "schedule": settings.reconcile_interval_seconds,
        },
    },
)

celery_app.autodiscover_tasks(["e2epool.tasks"])
