from fastapi import Depends, Header, HTTPException

from e2epool.backends.bare_metal import BareMetalBackend
from e2epool.backends.base import BackendProtocol
from e2epool.backends.proxmox import ProxmoxBackend
from e2epool.ci_adapters.base import CIAdapterProtocol
from e2epool.ci_adapters.gitlab import GitLabAdapter
from e2epool.config import settings
from e2epool.inventory import Inventory, RunnerConfig, load_inventory

_inventory: Inventory | None = None
_backends: dict[str, BackendProtocol] = {
    "proxmox": ProxmoxBackend(),
    "bare_metal": BareMetalBackend(),
}


def get_inventory() -> Inventory:
    global _inventory
    if _inventory is None:
        _inventory = load_inventory(settings.inventory_path)
    return _inventory


def set_inventory(inventory: Inventory) -> None:
    """Override inventory (for testing)."""
    global _inventory
    _inventory = inventory


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


def get_ci_adapter(runner: RunnerConfig) -> CIAdapterProtocol:
    # Global config takes priority
    if settings.gitlab_url and settings.gitlab_token:
        return GitLabAdapter(
            base_url=settings.gitlab_url,
            token=settings.gitlab_token,
        )

    # Fall back to per-runner config (backward compat)
    factory = _ci_adapter_factories.get(runner.ci_adapter)
    if factory is None:
        raise ValueError(f"Unknown CI adapter: {runner.ci_adapter}")
    kwargs = {
        "base_url": getattr(runner, f"{runner.ci_adapter}_url", "") or "",
        "token": getattr(runner, f"{runner.ci_adapter}_token", "") or "",
    }
    project_id = getattr(runner, f"{runner.ci_adapter}_project_id", None)
    if project_id is not None:
        kwargs["project_id"] = project_id
    return factory(**kwargs)


def register_ci_adapter(name: str, factory: type) -> None:
    """Register a CI adapter factory (for extensibility)."""
    _ci_adapter_factories[name] = factory


def verify_token(
    authorization: str = Header(...),
    inventory: Inventory = Depends(get_inventory),
) -> str:
    """Verify bearer token and return the runner_id it's scoped to."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization[7:]
    for rid in inventory.runner_ids:
        runner = inventory.get_runner(rid)
        if runner and runner.token == token:
            return rid

    raise HTTPException(status_code=403, detail="Invalid token")


def verify_ws_token(runner_id: str, token: str, inventory: Inventory) -> RunnerConfig:
    """Verify WebSocket token and return RunnerConfig.

    Raises ValueError if credentials are invalid.
    """
    runner = inventory.get_runner(runner_id)
    if not runner or runner.token != token:
        raise ValueError("Invalid credentials")
    return runner
