import hashlib
import hmac

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from e2epool.config import settings
from e2epool.database import get_db
from e2epool.models import Checkpoint
from e2epool.services.checkpoint_service import CheckpointError, queue_finalize
from e2epool.tasks.finalize import do_finalize

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

GITLAB_STATUS_MAP = {
    "success": "success",
    "failed": "failure",
    "canceled": "canceled",
}

GITHUB_CONCLUSION_MAP = {
    "success": "success",
    "failure": "failure",
    "cancelled": "canceled",
    "timed_out": "failure",
}


def verify_gitlab_token(request: Request) -> None:
    """Verify X-Gitlab-Token header matches configured secret."""
    token = request.headers.get("X-Gitlab-Token", "")
    if not hmac.compare_digest(token, settings.gitlab_webhook_secret or ""):
        raise HTTPException(403, "Invalid webhook token")


def verify_github_signature(body: bytes, signature: str) -> None:
    """Verify X-Hub-Signature-256 HMAC-SHA256 signature."""
    secret = settings.github_webhook_secret
    if not secret:
        raise HTTPException(403, "GitHub webhook secret not configured")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(403, "Invalid webhook signature")


@router.post("/gitlab")
async def gitlab_webhook(request: Request, db: Session = Depends(get_db)):
    verify_gitlab_token(request)

    body = await request.json()

    if body.get("object_kind") != "build":
        return {"ok": True}

    build_id = body.get("build_id")
    build_status = body.get("build_status")

    if not build_id or not build_status:
        return {"ok": True}

    status = GITLAB_STATUS_MAP.get(build_status)
    if not status:
        # Non-terminal status (running, pending, created, etc.)
        return {"ok": True}

    job_id = str(build_id)

    checkpoint = db.query(Checkpoint).filter(Checkpoint.job_id == job_id).first()
    if not checkpoint:
        logger.debug("Webhook: no checkpoint for job_id", job_id=job_id)
        return {"ok": True}

    if checkpoint.state != "created":
        logger.debug(
            "Webhook: checkpoint not in created state",
            checkpoint=checkpoint.name,
            state=checkpoint.state,
        )
        return {"ok": True}

    try:
        _, already = queue_finalize(db, checkpoint.name, status, source="webhook")
        if not already:
            do_finalize.delay(checkpoint.name)
            logger.info(
                "Webhook queued finalize",
                checkpoint=checkpoint.name,
                status=status,
                source="gitlab",
            )
    except CheckpointError:
        logger.exception("Webhook failed to queue finalize", checkpoint=checkpoint.name)

    return {"ok": True}


@router.post("/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    verify_github_signature(body_bytes, signature)

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type != "workflow_job":
        return {"ok": True}

    body = await request.json()

    action = body.get("action")
    if action != "completed":
        return {"ok": True}

    workflow_job = body.get("workflow_job", {})
    job_id_raw = workflow_job.get("id")
    conclusion = workflow_job.get("conclusion")

    if not job_id_raw or not conclusion:
        return {"ok": True}

    status = GITHUB_CONCLUSION_MAP.get(conclusion)
    if not status:
        return {"ok": True}

    job_id = str(job_id_raw)

    checkpoint = db.query(Checkpoint).filter(Checkpoint.job_id == job_id).first()
    if not checkpoint:
        logger.debug("Webhook: no checkpoint for job_id", job_id=job_id)
        return {"ok": True}

    if checkpoint.state != "created":
        logger.debug(
            "Webhook: checkpoint not in created state",
            checkpoint=checkpoint.name,
            state=checkpoint.state,
        )
        return {"ok": True}

    try:
        _, already = queue_finalize(db, checkpoint.name, status, source="webhook")
        if not already:
            do_finalize.delay(checkpoint.name)
            logger.info(
                "Webhook queued finalize",
                checkpoint=checkpoint.name,
                status=status,
                source="github",
            )
    except CheckpointError:
        logger.exception("Webhook failed to queue finalize", checkpoint=checkpoint.name)

    return {"ok": True}
