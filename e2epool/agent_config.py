import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class AgentConfig:
    controller_url: str = "ws://localhost:8080/ws/agent"
    runner_id: str = ""
    token: str = ""
    socket_path: str = "/var/run/e2epool-agent.sock"
    reconnect_max_delay: int = 60
    heartbeat_interval: int = 30


def load_agent_config(path: str | None = None) -> AgentConfig:
    """Load agent config from YAML file, then override with env vars."""
    config = AgentConfig()

    config_path = path or os.environ.get(
        "E2EPOOL_AGENT_CONFIG", "/etc/e2epool/agent.yml"
    )
    p = Path(config_path)
    if p.exists():
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        for key, val in data.items():
            if hasattr(config, key):
                setattr(config, key, val)

    env_map = {
        "E2EPOOL_CONTROLLER_URL": "controller_url",
        "E2EPOOL_RUNNER_ID": "runner_id",
        "E2EPOOL_TOKEN": "token",
        "E2EPOOL_SOCKET_PATH": "socket_path",
        "E2EPOOL_RECONNECT_MAX_DELAY": "reconnect_max_delay",
        "E2EPOOL_HEARTBEAT_INTERVAL": "heartbeat_interval",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            current = getattr(config, attr)
            if isinstance(current, int):
                setattr(config, attr, int(val))
            else:
                setattr(config, attr, val)

    return config
