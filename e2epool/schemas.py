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


# --- Runner admin schemas ---


def _parse_tags(v: list[str] | str | None) -> list[str]:
    """Parse tags from JSON string (DB) or pass through list (API input)."""
    if v is None:
        return []
    if isinstance(v, str):
        import json

        return json.loads(v)
    return v


class RunnerCreateRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=255, pattern=r"^[\w.\-]+$")
    backend: str = Field(min_length=1, max_length=50)

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

    # CI runner ID
    gitlab_runner_id: int | None = None

    # Common
    tags: list[str] = []


class RunnerListResponse(BaseModel):
    """Returned on list/get — omits token and proxmox_token_value."""

    runner_id: str
    backend: str

    proxmox_host: str | None = None
    proxmox_user: str | None = None
    proxmox_token_name: str | None = None
    proxmox_node: str | None = None
    proxmox_vmid: int | None = None

    reset_cmd: str | None = None
    cleanup_cmd: str | None = None
    readiness_cmd: str | None = None

    gitlab_runner_id: int | None = None
    tags: list[str] = []
    is_active: bool = True
    created_at: datetime.datetime
    updated_at: datetime.datetime

    _parse_tags = field_validator("tags", mode="before")(staticmethod(_parse_tags))

    model_config = {"from_attributes": True}


class RunnerResponse(RunnerListResponse):
    """Returned on creation — includes token."""

    token: str
