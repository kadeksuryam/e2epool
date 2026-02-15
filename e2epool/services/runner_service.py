"""CRUD service layer for DB-backed runner registry."""

import dataclasses
import json
import secrets

from sqlalchemy.orm import Session

from e2epool.inventory import RunnerConfig
from e2epool.models import Runner

REQUIRED_PROXMOX_FIELDS = [
    "proxmox_host",
    "proxmox_user",
    "proxmox_token_name",
    "proxmox_token_value",
    "proxmox_node",
    "proxmox_vmid",
]

# RunnerConfig field names (minus 'tags' which needs JSON handling)
_CONFIG_FIELDS = [f.name for f in dataclasses.fields(RunnerConfig) if f.name != "tags"]


def validate_runner_fields(backend: str, data: dict) -> None:
    """Validate backend-specific required fields.

    Raises ValueError with descriptive message on failure.
    """
    if backend not in ("proxmox", "bare_metal"):
        raise ValueError(
            f"Invalid backend '{backend}'. Must be 'proxmox' or 'bare_metal'."
        )

    if backend == "bare_metal" and not data.get("reset_cmd"):
        raise ValueError("bare_metal backend requires 'reset_cmd'.")

    if backend == "proxmox":
        missing = [f for f in REQUIRED_PROXMOX_FIELDS if not data.get(f)]
        if missing:
            raise ValueError(
                f"proxmox backend is missing required fields: {', '.join(missing)}"
            )


def create_runner(db: Session, data: dict) -> Runner:
    """Insert a new runner row or reactivate a deactivated one.

    Auto-generates a new token. If the runner_id exists but is deactivated,
    reactivates it with the new data. Raises IntegrityError if runner_id
    exists and is active (caller should handle as 409).
    """
    validate_runner_fields(data["backend"], data)

    tags = data.pop("tags", [])
    tags_json = json.dumps(tags) if tags else None

    # Check for existing deactivated runner
    existing = (
        db.query(Runner)
        .filter(Runner.runner_id == data["runner_id"], Runner.is_active.is_(False))
        .first()
    )
    if existing:
        for key, val in data.items():
            setattr(existing, key, val)
        existing.token = secrets.token_urlsafe(32)
        existing.tags = tags_json
        existing.is_active = True
        db.flush()
        return existing

    runner = Runner(
        **data,
        token=secrets.token_urlsafe(32),
        tags=tags_json,
    )
    db.add(runner)
    db.flush()
    return runner


def list_runners(db: Session, include_inactive: bool = False) -> list[Runner]:
    q = db.query(Runner)
    if not include_inactive:
        q = q.filter(Runner.is_active.is_(True))
    return q.order_by(Runner.runner_id).all()


def get_runner_by_id(db: Session, runner_id: str) -> Runner | None:
    return (
        db.query(Runner)
        .filter(Runner.runner_id == runner_id, Runner.is_active.is_(True))
        .first()
    )


def deactivate_runner(db: Session, runner_id: str) -> Runner | None:
    """Soft-delete: set is_active=False. Returns the runner or None."""
    runner = get_runner_by_id(db, runner_id)
    if runner is None:
        return None
    runner.is_active = False
    db.flush()
    return runner


def runner_to_config(runner: Runner) -> RunnerConfig:
    """Convert a DB Runner row to a RunnerConfig dataclass."""
    data = {f: getattr(runner, f) for f in _CONFIG_FIELDS}
    data["tags"] = json.loads(runner.tags) if runner.tags else []
    return RunnerConfig(**data)


def config_to_runner(config: RunnerConfig) -> Runner:
    """Convert a RunnerConfig dataclass to a Runner model instance."""
    data = dataclasses.asdict(config)
    tags = data.pop("tags", [])
    data["tags"] = json.dumps(tags) if tags else None
    return Runner(**data)
