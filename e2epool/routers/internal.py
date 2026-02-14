import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from e2epool.services.ws_manager import ws_manager

logger = structlog.get_logger()

router = APIRouter(prefix="/internal", tags=["internal"])


class ExecRequest(BaseModel):
    cmd: str
    timeout: float = 120.0


class ExecResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


class ConnectedResponse(BaseModel):
    connected: bool


@router.post(
    "/agent/{runner_id}/exec",
    response_model=ExecResponse,
)
async def agent_exec(runner_id: str, req: ExecRequest):
    """Execute a command on a connected agent."""
    if not ws_manager.is_connected(runner_id):
        raise HTTPException(status_code=503, detail=f"Agent {runner_id} not connected")

    try:
        result = await ws_manager.send_command(
            runner_id,
            {"cmd": req.cmd, "timeout": req.timeout},
            timeout=req.timeout + 5,
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail=f"Agent {runner_id} timed out")
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    data = result.get("data", {})
    exit_code = data.get("exit_code", -1)
    stdout = data.get("stdout", "")
    stderr = data.get("stderr", "")

    if result.get("status") != "ok":
        raise HTTPException(
            status_code=502,
            detail=f"Command failed (exit {exit_code}): {stderr}",
        )

    return ExecResponse(exit_code=exit_code, stdout=stdout, stderr=stderr)


@router.get(
    "/agent/{runner_id}/connected",
    response_model=ConnectedResponse,
)
async def agent_connected(runner_id: str):
    """Check if an agent is connected."""
    return ConnectedResponse(connected=ws_manager.is_connected(runner_id))
