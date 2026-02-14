"""Tests for PostgreSQL advisory locking functionality."""

import os

from e2epool.locking import acquire_lock, release_lock, runner_lock_id


class TestRunnerLockId:
    """Tests for the runner_lock_id function."""

    def test_lock_id_deterministic(self):
        """Same runner_id should always return the same lock_id."""
        runner_id = "test-runner-01"
        lock_id_1 = runner_lock_id(runner_id)
        lock_id_2 = runner_lock_id(runner_id)
        assert lock_id_1 == lock_id_2

    def test_lock_id_different_runners(self):
        """Different runner_ids should return different lock_ids."""
        lock_id_1 = runner_lock_id("runner-01")
        lock_id_2 = runner_lock_id("runner-02")
        assert lock_id_1 != lock_id_2

    def test_lock_id_fits_int32(self):
        """Lock ID should fit within int32 range (0 to 0x7FFFFFFF)."""
        for runner_id in ["runner-1", "runner-2", "test-runner", "prod-runner-99"]:
            lock_id = runner_lock_id(runner_id)
            assert 0 <= lock_id <= 0x7FFFFFFF


class TestAdvisoryLocking:
    """Tests for advisory lock acquisition and release."""

    def test_acquire_and_release_lock(self, db_session_factory):
        """
        Acquire lock in session1, verify session2 cannot acquire it,
        then release in session1 and verify session2 can acquire it.
        """
        runner_id = "test-runner-lock-01"

        session1 = db_session_factory()
        session2 = db_session_factory()

        try:
            # Session 1 acquires the lock
            acquired_1 = acquire_lock(session1, runner_id)
            assert acquired_1 is True

            # Session 2 tries to acquire the same lock (should fail)
            acquired_2 = acquire_lock(session2, runner_id)
            assert acquired_2 is False

            # Session 1 releases the lock
            released_1 = release_lock(session1, runner_id)
            assert released_1 is True

            # Session 2 tries to acquire the lock again (should succeed)
            acquired_3 = acquire_lock(session2, runner_id)
            assert acquired_3 is True

            # Clean up: release from session2
            release_lock(session2, runner_id)

        finally:
            session1.close()
            session2.close()

    def test_lock_released_on_connection_close(self):
        """
        Acquire lock via a raw connection, close it,
        then verify another session can acquire the same lock.

        Advisory locks are connection-level. We use raw connections to ensure
        the lock is truly released when the connection is closed.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        runner_id = "test-runner-lock-02"
        url = os.environ["E2EPOOL_DATABASE_URL"]

        # Use a separate engine with pool_size=1 so closing is deterministic
        engine1 = create_engine(url, pool_size=1, max_overflow=0)
        engine2 = create_engine(url, pool_size=1, max_overflow=0)

        session1 = Session(bind=engine1)
        session2 = Session(bind=engine2)

        try:
            # Session 1 acquires the lock
            acquired_1 = acquire_lock(session1, runner_id)
            assert acquired_1 is True

            # Close session and dispose the engine (closes the connection)
            session1.close()
            engine1.dispose()

            # Session 2 can now acquire the same lock
            acquired_2 = acquire_lock(session2, runner_id)
            assert acquired_2 is True

            release_lock(session2, runner_id)
        finally:
            session2.close()
            engine2.dispose()
