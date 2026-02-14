from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://e2epool:e2epool@localhost:5432/e2epool"
    redis_url: str = "redis://localhost:6379/0"
    inventory_path: str = "inventory.yml"

    # GC settings
    checkpoint_ttl_seconds: int = 1800  # 30 minutes
    gc_interval_seconds: int = 60

    # Poller settings
    poller_interval_seconds: int = 20
    poller_min_age_seconds: int = 120  # skip checkpoints < 2 min old

    # Reconcile settings
    reconcile_interval_seconds: int = 120

    # Finalize settings
    finalize_cooldown_seconds: int = 5

    # Readiness
    readiness_timeout_seconds: int = 120
    readiness_poll_interval_seconds: int = 5

    # Database connection pool
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_recycle: int = 1800

    # Task timeouts
    task_soft_time_limit: int = 300
    task_hard_time_limit: int = 330
    poller_soft_time_limit: int = 120
    poller_hard_time_limit: int = 150

    # WebSocket
    ws_heartbeat_interval: int = 30
    ws_heartbeat_timeout: int = 90

    # HTTP client
    httpx_timeout: int = 30

    # Internal API base URL (for agent RPC from Celery workers)
    api_base_url: str = "http://127.0.0.1:8080"

    # CI adapter (global, used for pause/unpause + poller fallback)
    gitlab_url: str | None = None
    gitlab_token: str | None = None

    # Webhook secrets
    gitlab_webhook_secret: str | None = None
    github_webhook_secret: str | None = None

    # Poller toggle (disable when webhooks are configured)
    poller_enabled: bool = True

    # Batch processing
    query_batch_size: int = 200

    model_config = {"env_prefix": "E2EPOOL_"}


settings = Settings()
