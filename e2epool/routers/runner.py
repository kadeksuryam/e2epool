from fastapi import APIRouter, Depends, HTTPException

from e2epool.dependencies import get_backend, get_inventory, verify_token
from e2epool.schemas import ReadinessResponse

router = APIRouter(prefix="/runner", tags=["runner"])


@router.get("/readiness", response_model=ReadinessResponse)
def readiness(
    runner_id: str = Depends(verify_token),
    inventory=Depends(get_inventory),
):
    runner = inventory.get_runner(runner_id)
    if not runner:
        raise HTTPException(status_code=404, detail="Runner not found")

    backend = get_backend(runner)
    try:
        ready = backend.check_ready(runner)
    except Exception as e:
        return ReadinessResponse(runner_id=runner_id, ready=False, detail=str(e))

    if not ready:
        raise HTTPException(status_code=503, detail="Runner not ready")
    return ReadinessResponse(runner_id=runner_id, ready=True)
