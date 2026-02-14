import zlib

from sqlalchemy import text
from sqlalchemy.orm import Session


def runner_lock_id(runner_id: str) -> int:
    """Compute a deterministic int32 lock ID from a runner_id using CRC32."""
    return zlib.crc32(runner_id.encode()) & 0x7FFFFFFF


def acquire_lock(session: Session, runner_id: str) -> bool:
    """Acquire a PostgreSQL advisory lock (session-level). Returns True if acquired."""
    lock_id = runner_lock_id(runner_id)
    result = session.execute(
        text("SELECT pg_try_advisory_lock(:lock_id)"),
        {"lock_id": lock_id},
    )
    return result.scalar()


def release_lock(session: Session, runner_id: str) -> bool:
    """Release a PostgreSQL advisory lock (session-level). Returns True if released."""
    lock_id = runner_lock_id(runner_id)
    result = session.execute(
        text("SELECT pg_advisory_unlock(:lock_id)"),
        {"lock_id": lock_id},
    )
    return result.scalar()
