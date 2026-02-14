import structlog
from sqlalchemy.orm import Session

from e2epool.backends.base import BackendProtocol
from e2epool.dependencies import get_backend
from e2epool.inventory import RunnerConfig
from e2epool.schemas import CheckpointResponse, WSRequest, WSResponse
from e2epool.services.checkpoint_service import (
    CheckpointError,
    create_checkpoint,
    get_checkpoint_by_name,
    queue_finalize,
)
from e2epool.tasks.finalize import do_finalize

logger = structlog.get_logger()


def handle_message(
    request: WSRequest,
    runner: RunnerConfig,
    db: Session,
) -> WSResponse:
    """Dispatch a WebSocket message to the appropriate service function."""
    try:
        if request.type == "ping":
            return WSResponse(id=request.id, status="ok", data={"pong": True})

        if request.type == "create":
            return _handle_create(request, runner, db)

        if request.type == "finalize":
            return _handle_finalize(request, runner, db)

        if request.type == "status":
            return _handle_status(request, runner, db)

    except CheckpointError as e:
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": e.status_code, "detail": e.detail},
        )
    except Exception:
        logger.exception("Unexpected error handling WS message", type=request.type)
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": 500, "detail": "Internal server error"},
        )


def _handle_create(request: WSRequest, runner: RunnerConfig, db: Session) -> WSResponse:
    job_id = request.payload.get("job_id", "")
    caller = request.payload.get("caller")
    if not job_id:
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": 400, "detail": "job_id is required"},
        )

    backend: BackendProtocol = get_backend(runner)
    checkpoint = create_checkpoint(db, runner, job_id, backend, caller)
    data = CheckpointResponse.model_validate(checkpoint).model_dump(mode="json")
    return WSResponse(id=request.id, status="ok", data=data)


def _handle_finalize(
    request: WSRequest, runner: RunnerConfig, db: Session
) -> WSResponse:
    checkpoint_name = request.payload.get("checkpoint_name", "")
    status = request.payload.get("status", "")
    source = request.payload.get("source", "agent")
    if not checkpoint_name or not status:
        return WSResponse(
            id=request.id,
            status="error",
            error={
                "code": 400,
                "detail": "checkpoint_name and status are required",
            },
        )

    cp = get_checkpoint_by_name(db, checkpoint_name)
    if not cp:
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": 404, "detail": "Checkpoint not found"},
        )
    if cp.runner_id != runner.runner_id:
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": 403, "detail": "Not authorized for this checkpoint"},
        )

    checkpoint, already = queue_finalize(db, checkpoint_name, status, source)
    if already:
        return WSResponse(
            id=request.id,
            status="ok",
            data={"detail": "Already finalized", "state": checkpoint.state},
        )

    try:
        do_finalize.delay(checkpoint.name)
    except Exception:
        logger.exception(
            "Failed to enqueue finalize task via WS", checkpoint=checkpoint.name
        )
        return WSResponse(
            id=request.id,
            status="error",
            error={
                "code": 503,
                "detail": "Finalize queued in DB but task broker unavailable. "
                "The task will be retried on next reconciliation.",
            },
        )

    return WSResponse(
        id=request.id,
        status="ok",
        data={"detail": "Finalize queued", "checkpoint_name": checkpoint.name},
    )


def _handle_status(request: WSRequest, runner: RunnerConfig, db: Session) -> WSResponse:
    checkpoint_name = request.payload.get("checkpoint_name", "")
    if not checkpoint_name:
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": 400, "detail": "checkpoint_name is required"},
        )

    cp = get_checkpoint_by_name(db, checkpoint_name)
    if not cp:
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": 404, "detail": "Checkpoint not found"},
        )
    if cp.runner_id != runner.runner_id:
        return WSResponse(
            id=request.id,
            status="error",
            error={"code": 403, "detail": "Not authorized for this checkpoint"},
        )

    data = CheckpointResponse.model_validate(cp).model_dump(mode="json")
    return WSResponse(id=request.id, status="ok", data=data)
