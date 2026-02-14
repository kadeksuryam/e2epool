import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from e2epool.database import get_db
from e2epool.dependencies import get_backend, get_inventory, verify_token
from e2epool.schemas import (
    CheckpointCreateRequest,
    CheckpointFinalizeRequest,
    CheckpointResponse,
)
from e2epool.services.checkpoint_service import (
    CheckpointError,
    create_checkpoint,
    get_checkpoint_by_name,
    queue_finalize,
)
from e2epool.tasks.finalize import do_finalize

logger = structlog.get_logger()

router = APIRouter(prefix="/checkpoint", tags=["checkpoint"])


@router.post("/create", response_model=CheckpointResponse, status_code=201)
def create(
    body: CheckpointCreateRequest,
    runner_id: str = Depends(verify_token),
    db: Session = Depends(get_db),
    inventory=Depends(get_inventory),
):
    runner = inventory.get_runner(body.runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found in inventory")

    if runner.runner_id != runner_id:
        raise HTTPException(
            status_code=403, detail="Token not authorized for this runner"
        )

    backend = get_backend(runner)
    try:
        checkpoint = create_checkpoint(db, runner, body.job_id, backend, body.caller)
    except CheckpointError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    return CheckpointResponse.model_validate(checkpoint)


@router.post("/finalize", status_code=202)
def finalize(
    body: CheckpointFinalizeRequest,
    runner_id: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    checkpoint = get_checkpoint_by_name(db, body.checkpoint_name)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    if checkpoint.runner_id != runner_id:
        raise HTTPException(
            status_code=403, detail="Token not authorized for this checkpoint"
        )

    try:
        checkpoint, already = queue_finalize(
            db, body.checkpoint_name, body.status.value, body.source
        )
    except CheckpointError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    if already:
        return {"detail": "Already finalized", "state": checkpoint.state}

    try:
        do_finalize.delay(checkpoint.name)
    except Exception:
        logger.exception("Failed to enqueue finalize task", checkpoint=checkpoint.name)
        raise HTTPException(
            status_code=503,
            detail="Finalize queued in DB but task broker unavailable. "
            "The task will be retried on next reconciliation.",
        )
    return {"detail": "Finalize queued", "checkpoint_name": checkpoint.name}


@router.get("/status/{checkpoint_name}", response_model=CheckpointResponse)
def status(
    checkpoint_name: str,
    runner_id: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    checkpoint = get_checkpoint_by_name(db, checkpoint_name)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    if checkpoint.runner_id != runner_id:
        raise HTTPException(
            status_code=403, detail="Token not authorized for this checkpoint"
        )
    return CheckpointResponse.model_validate(checkpoint)
