"""Admin API for runner CRUD operations."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from e2epool.database import get_db
from e2epool.dependencies import verify_admin_token
from e2epool.schemas import RunnerCreateRequest, RunnerListResponse, RunnerResponse
from e2epool.services import runner_service

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/runners",
    tags=["admin"],
    dependencies=[Depends(verify_admin_token)],
)


@router.post("", status_code=201, response_model=RunnerResponse)
def create_runner(body: RunnerCreateRequest, db: Session = Depends(get_db)):
    data = body.model_dump()
    try:
        runner = runner_service.create_runner(db, data)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Runner '{body.runner_id}' already exists"
        )
    db.refresh(runner)
    logger.info("Runner created", runner_id=runner.runner_id)
    return RunnerResponse.model_validate(runner)


@router.get("", response_model=list[RunnerListResponse])
def list_runners(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    runners = runner_service.list_runners(db, include_inactive=include_inactive)
    return [RunnerListResponse.model_validate(r) for r in runners]


@router.get("/{runner_id}", response_model=RunnerListResponse)
def get_runner(runner_id: str, db: Session = Depends(get_db)):
    runner = runner_service.get_runner_by_id(db, runner_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found")
    return RunnerListResponse.model_validate(runner)


@router.delete("/{runner_id}")
def delete_runner(runner_id: str, db: Session = Depends(get_db)):
    runner = runner_service.deactivate_runner(db, runner_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="Runner not found")
    db.commit()
    logger.info("Runner deactivated", runner_id=runner_id)
    return {"detail": f"Runner '{runner_id}' deactivated"}
