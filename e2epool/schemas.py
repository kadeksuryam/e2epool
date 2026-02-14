import datetime
import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class FinalizeStatus(str, Enum):
    success = "success"
    failure = "failure"
    canceled = "canceled"


CHECKPOINT_NAME_PATTERN = re.compile(r"^job-[\w.\-]+-\d+-[0-9a-f]{8}$")


class CheckpointCreateRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=255, pattern=r"^[\w.\-]+$")
    job_id: str = Field(min_length=1, max_length=255, pattern=r"^[\w.\-]+$")
    caller: str | None = Field(default=None, max_length=255)


class CheckpointFinalizeRequest(BaseModel):
    checkpoint_name: str
    status: FinalizeStatus
    source: str = Field(
        default="hook", min_length=1, max_length=100, pattern=r"^[\w.\-]+$"
    )

    @field_validator("checkpoint_name")
    @classmethod
    def validate_checkpoint_name(cls, v: str) -> str:
        if not CHECKPOINT_NAME_PATTERN.match(v):
            raise ValueError(
                f"checkpoint_name must match pattern {CHECKPOINT_NAME_PATTERN.pattern}"
            )
        return v


class CheckpointResponse(BaseModel):
    name: str
    runner_id: str
    job_id: str
    state: str
    finalize_status: str | None = None
    finalize_source: str | None = None
    created_at: datetime.datetime
    finalized_at: datetime.datetime | None = None

    model_config = {"from_attributes": True}


class WSRequest(BaseModel):
    id: str
    type: Literal["create", "finalize", "status", "ping"]
    payload: dict = {}


class WSResponse(BaseModel):
    id: str
    status: Literal["ok", "error"]
    data: dict | None = None
    error: dict | None = None  # {"code": int, "detail": str}


class ReadinessResponse(BaseModel):
    runner_id: str
    ready: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    detail: str | None = None
