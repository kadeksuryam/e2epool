import hmac
import time

import structlog
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from e2epool.backends.bare_metal import BareMetalBackend
from e2epool.backends.base import BackendProtocol
from e2epool.backends.proxmox import ProxmoxBackend
from e2epool.ci_adapters.base import CIAdapterProtocol
from e2epool.ci_adapters.gitlab import GitLabAdapter
from e2epool.config import settings
from e2epool.database import SessionLocal, get_db
from e2epool.inventory import Inventory, RunnerConfig, load_inventory
from e2epool.models import Runner
from e2epool.services.runner_service import runner_to_config

logger = structlog.get_logger()

_inventory: Inventory | None = None
_inventory_ts: float = 0.0
_INVENTORY_TTL = 5.0  # seconds

_backends: dict[str, BackendProtocol] = {
    "proxmox": ProxmoxBackend(),
    "bare_metal": BareMetalBackend(),
}


def _load_inventory_from_db(db: Session) -> Inventory:
    """Query all active Runner rows and build an Inventory."""
    rows = db.query(Runner).filter(Runner.is_active.is_(True)).all()
    runners = {}
    for row in rows:
        runners[row.runner_id] = runner_to_config(row)
    return Inventory(runners)


def get_inventory() -> Inventory:
    """Return an Inventory backed by DB with TTL cache.

    Falls back to stale cache if DB unavailable, then to YAML as last resort.
    """
    global _inventory, _inventory_ts

    now = time.monotonic()
    if _inventory is not None and (now - _inventory_ts) < _INVENTORY_TTL:
        return _inventory

    try:
        db = SessionLocal()
        try:
            inv = _load_inventory_from_db(db)
        finally:
            db.close()
        _inventory = inv
        _inventory_ts = now
        return _inventory
    except Exception:
        # DB unavailable — return stale cache if available
        if _inventory is not None:
            logger.warning("DB unavailable, using stale inventory cache")
            return _inventory

        # Last resort: fall back to YAML
        logger.warning("DB unavailable and no cache, falling back to YAML inventory")
        _inventory = load_inventory(settings.inventory_path)
        _inventory_ts = now
        return _inventory


def set_inventory(inventory: Inventory) -> None:
    """Override inventory (for testing)."""
    global _inventory, _inventory_ts
    _inventory = inventory
    _inventory_ts = time.monotonic()


def get_backend(runner: RunnerConfig) -> BackendProtocol:
    backend = _backends.get(runner.backend)
    if backend is None:
        raise ValueError(f"Unknown backend: {runner.backend}")
    return backend


def set_backends(backends: dict[str, BackendProtocol]) -> None:
    """Override backends (for testing)."""
    global _backends
    _backends = backends


_ci_adapter_factories: dict[str, type] = {
    "gitlab": GitLabAdapter,
}


def get_ci_adapter() -> CIAdapterProtocol:
    """Build a CI adapter from global config.

    Each adapter reads its own provider-specific settings
    (e.g. gitlab_url/gitlab_token for GitLab).
    """
    factory = _ci_adapter_factories.get(settings.ci_provider)
    if factory is None:
        raise ValueError(f"Unknown CI provider: {settings.ci_provider}")
    return factory()


def register_ci_adapter(name: str, factory: type) -> None:
    """Register a CI adapter factory (for extensibility)."""
    _ci_adapter_factories[name] = factory


def verify_admin_token(authorization: str = Header(...)) -> None:
    """Verify admin bearer token for admin API endpoints."""
    if not settings.admin_token:
        raise HTTPException(
            status_code=503, detail="Admin API not configured"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization[7:]
    if not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token")


def verify_token(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
) -> str:
    """Verify bearer token via direct DB query and return runner_id."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization[7:]
    runner = (
        db.query(Runner)
        .filter(Runner.token == token, Runner.is_active.is_(True))
        .first()
    )
    if runner is None:
        raise HTTPException(status_code=403, detail="Invalid token")
    return runner.runner_id


def verify_ws_token(runner_id: str, token: str, db: Session) -> RunnerConfig:
    """Verify WebSocket token via DB query and return RunnerConfig.

    Raises ValueError if credentials are invalid.
    """
    runner = (
        db.query(Runner)
        .filter(
            Runner.runner_id == runner_id,
            Runner.token == token,
            Runner.is_active.is_(True),
        )
        .first()
    )
    if runner is None:
        raise ValueError("Invalid credentials")
    return runner_to_config(runner)
