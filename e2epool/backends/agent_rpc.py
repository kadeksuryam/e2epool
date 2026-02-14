"""Sync helpers that call the internal HTTP API to execute commands on agents."""

import time

import httpx

from e2epool.config import settings


class AgentError(RuntimeError):
    """Command execution failed on the agent."""


class AgentNotConnected(AgentError):
    """Agent is not connected to the controller."""


def run_on_agent(runner_id: str, cmd: str, timeout: int = 120) -> str:
    """Execute a command on a runner via the WebSocket agent. Returns stdout."""
    url = f"{settings.api_base_url}/internal/agent/{runner_id}/exec"
    try:
        resp = httpx.post(
            url,
            json={"cmd": cmd, "timeout": timeout},
            timeout=timeout + 10,
        )
    except httpx.TimeoutException:
        raise AgentError(f"HTTP request to agent {runner_id} timed out")

    if resp.status_code == 503:
        raise AgentNotConnected(f"Agent {runner_id} not connected")
    if resp.status_code == 504:
        raise AgentError(f"Agent {runner_id} command timed out")
    if resp.status_code != 200:
        detail = resp.json().get("detail", resp.text) if resp.text else ""
        raise AgentError(
            f"Agent {runner_id} command failed (HTTP {resp.status_code}): {detail}"
        )

    return resp.json().get("stdout", "")


def wait_for_agent(runner_id: str, timeout: int | None = None) -> bool:
    """Poll until the agent is connected. Returns True or raises TimeoutError."""
    if timeout is None:
        timeout = settings.readiness_timeout_seconds

    url = f"{settings.api_base_url}/internal/agent/{runner_id}/connected"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code == 200 and resp.json().get("connected"):
                return True
        except httpx.HTTPError:
            pass
        time.sleep(settings.readiness_poll_interval_seconds)

    raise TimeoutError(f"Agent {runner_id} not connected after {timeout}s")
