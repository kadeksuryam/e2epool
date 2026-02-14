import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RunnerConfig:
    runner_id: str
    backend: str  # "proxmox" or "bare_metal"
    token: str

    # Proxmox-specific
    proxmox_host: str | None = None
    proxmox_user: str | None = None
    proxmox_token_name: str | None = None
    proxmox_token_value: str | None = None
    proxmox_node: str | None = None
    proxmox_vmid: int | None = None

    # Bare-metal specific
    reset_cmd: str | None = None
    cleanup_cmd: str | None = None
    readiness_cmd: str | None = None

    # CI runner ID for pause/unpause
    ci_runner_id: int | None = None

    # Common
    tags: list[str] = field(default_factory=list)


class Inventory:
    def __init__(self, runners: dict[str, RunnerConfig]):
        self._runners = runners

    def get_runner(self, runner_id: str) -> RunnerConfig | None:
        return self._runners.get(runner_id)

    def get_all_runners(self) -> dict[str, RunnerConfig]:
        return dict(self._runners)

    @property
    def runner_ids(self) -> list[str]:
        return list(self._runners.keys())


def load_inventory(path: str | Path) -> Inventory:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Inventory file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    known_fields = {f.name for f in dataclasses.fields(RunnerConfig)}

    runners: dict[str, RunnerConfig] = {}
    for runner_data in data.get("runners", []):
        runner_id = runner_data["runner_id"]
        backend = runner_data.get("backend")

        if backend not in ("proxmox", "bare_metal"):
            raise ValueError(
                f"Invalid backend '{backend}' for runner '{runner_id}'. "
                "Must be 'proxmox' or 'bare_metal'."
            )

        if backend == "bare_metal" and not runner_data.get("reset_cmd"):
            raise ValueError(
                f"Runner '{runner_id}' with bare_metal backend requires 'reset_cmd'."
            )

        if backend == "proxmox":
            required_proxmox = [
                "proxmox_host",
                "proxmox_user",
                "proxmox_token_name",
                "proxmox_token_value",
                "proxmox_node",
                "proxmox_vmid",
            ]
            missing = [f for f in required_proxmox if not runner_data.get(f)]
            if missing:
                raise ValueError(
                    f"Runner '{runner_id}' with proxmox backend is missing "
                    f"required fields: {', '.join(missing)}"
                )

        filtered = {k: v for k, v in runner_data.items() if k in known_fields}
        runners[runner_id] = RunnerConfig(**filtered)

    return Inventory(runners)
