"""CLI for e2epool agent and checkpoint commands."""

import sys
import uuid

import click

from e2epool.agent_config import load_agent_config
from e2epool.ipc import IPCClient


@click.group()
def main():
    """e2epool — WebSocket agent and checkpoint CLI."""


@main.command()
@click.option("--config", default=None, help="Path to agent config YAML.")
def agent(config):
    """Start the e2epool agent daemon (foreground)."""
    import asyncio

    from e2epool.agent import Agent
    from e2epool.agent_config import load_agent_config

    cfg = load_agent_config(config)
    if not cfg.runner_id or not cfg.token:
        click.echo("Error: runner_id and token must be configured", err=True)
        sys.exit(1)

    a = Agent(cfg)
    asyncio.run(a.run())


@main.command()
@click.option("--job-id", required=True, help="CI job identifier.")
@click.option("--socket", default=None, help="Agent IPC socket path.")
def create(job_id, socket):
    """Create a checkpoint via the local agent."""
    cfg = load_agent_config()
    sock_path = socket or cfg.socket_path
    msg = {
        "id": uuid.uuid4().hex[:8],
        "type": "create",
        "payload": {"job_id": job_id},
    }
    result = _ipc_request(sock_path, msg)
    if result["status"] == "ok" and result.get("data"):
        click.echo(result["data"]["name"])
    else:
        _print_error(result)
        sys.exit(1)


@main.command()
@click.option("--checkpoint", required=True, help="Checkpoint name.")
@click.option(
    "--status",
    "finalize_status",
    required=True,
    type=click.Choice(["success", "failure", "canceled"]),
    help="Job outcome.",
)
@click.option("--socket", default=None, help="Agent IPC socket path.")
def finalize(checkpoint, finalize_status, socket):
    """Finalize a checkpoint via the local agent."""
    cfg = load_agent_config()
    sock_path = socket or cfg.socket_path
    msg = {
        "id": uuid.uuid4().hex[:8],
        "type": "finalize",
        "payload": {
            "checkpoint_name": checkpoint,
            "status": finalize_status,
            "source": "agent",
        },
    }
    result = _ipc_request(sock_path, msg)
    if result["status"] == "ok":
        detail = result.get("data", {}).get("detail", "OK")
        click.echo(detail)
    else:
        _print_error(result)
        sys.exit(1)


@main.command()
@click.option("--checkpoint", required=True, help="Checkpoint name.")
@click.option("--socket", default=None, help="Agent IPC socket path.")
def status(checkpoint, socket):
    """Query checkpoint status via the local agent."""
    cfg = load_agent_config()
    sock_path = socket or cfg.socket_path
    msg = {
        "id": uuid.uuid4().hex[:8],
        "type": "status",
        "payload": {"checkpoint_name": checkpoint},
    }
    result = _ipc_request(sock_path, msg)
    if result["status"] == "ok" and result.get("data"):
        data = result["data"]
        click.echo(f"name:   {data.get('name', '')}")
        click.echo(f"state:  {data.get('state', '')}")
        if data.get("finalize_status"):
            click.echo(f"result: {data['finalize_status']}")
    else:
        _print_error(result)
        sys.exit(1)


def _ipc_request(socket_path: str, msg: dict) -> dict:
    client = IPCClient(socket_path)
    try:
        return client.request(msg)
    except FileNotFoundError:
        click.echo("Error: agent is not running (socket not found)", err=True)
        sys.exit(2)
    except ConnectionRefusedError:
        click.echo("Error: agent is not running (connection refused)", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _print_error(result: dict) -> None:
    err = result.get("error", {})
    if isinstance(err, dict):
        click.echo(f"Error: {err.get('detail', 'Unknown error')}", err=True)
    else:
        click.echo(f"Error: {err}", err=True)
