import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship

from e2epool.database import Base

ACTIVE_STATES = ("created", "finalize_queued")
TERMINAL_STATES = ("reset", "deleted", "gc_reset")
ALL_STATES = ("created", "finalize_queued", "reset", "deleted", "gc_reset")
FINALIZE_STATUSES = ("success", "failure", "canceled")


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    runner_id = Column(String(255), nullable=False, index=True)
    job_id = Column(String(255), nullable=False)
    state = Column(String(50), nullable=False, default="created")
    finalize_status = Column(String(50), nullable=True)
    finalize_source = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    finalized_at = Column(DateTime, nullable=True)

    operation_logs = relationship(
        "OperationLog", back_populates="checkpoint", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            f"state IN {ALL_STATES!r}",
            name="ck_checkpoint_state",
        ),
        CheckConstraint(
            f"finalize_status IS NULL OR finalize_status IN {FINALIZE_STATUSES!r}",
            name="ck_checkpoint_finalize_status",
        ),
        # Partial unique index: only one active checkpoint per runner.
        Index(
            "ix_one_active_checkpoint_per_runner",
            runner_id,
            unique=True,
            postgresql_where=text("state IN ('created', 'finalize_queued')"),
        ),
        # Partial index for GC: efficiently find stale 'created' checkpoints.
        Index(
            "ix_checkpoints_gc",
            "created_at",
            postgresql_where=text("state = 'created'"),
        ),
    )


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    checkpoint_id = Column(
        Integer, ForeignKey("checkpoints.id"), nullable=False, index=True
    )
    runner_id = Column(String(255), nullable=False)
    operation = Column(String(100), nullable=False)
    backend = Column(String(50), nullable=True)
    detail = Column(Text, nullable=True)
    result = Column(String(50), nullable=True)
    started_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    checkpoint = relationship("Checkpoint", back_populates="operation_logs")


VALID_BACKENDS = ("proxmox", "bare_metal")


class Runner(Base):
    __tablename__ = "runners"

    id = Column(Integer, primary_key=True, autoincrement=True)
    runner_id = Column(String(255), unique=True, nullable=False)
    backend = Column(String(50), nullable=False)
    token = Column(String(255), unique=True, nullable=False)

    # Proxmox-specific
    proxmox_host = Column(String(255), nullable=True)
    proxmox_user = Column(String(255), nullable=True)
    proxmox_token_name = Column(String(255), nullable=True)
    proxmox_token_value = Column(String(255), nullable=True)
    proxmox_node = Column(String(255), nullable=True)
    proxmox_vmid = Column(Integer, nullable=True)

    # Bare-metal specific
    reset_cmd = Column(Text, nullable=True)
    cleanup_cmd = Column(Text, nullable=True)
    readiness_cmd = Column(Text, nullable=True)

    # CI runner ID for pause/unpause
    gitlab_runner_id = Column(Integer, nullable=True)

    # Common
    tags = Column(Text, nullable=True)  # JSON-encoded list

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    __table_args__ = (
        CheckConstraint(
            f"backend IN {VALID_BACKENDS!r}",
            name="ck_runner_backend",
        ),
        Index("ix_runners_runner_id", "runner_id", unique=True),
        Index("ix_runners_token", "token", unique=True),
    )
