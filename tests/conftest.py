import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault(
    "E2EPOOL_DATABASE_URL",
    "postgresql://e2epool:e2epool@localhost:5434/e2epool_test",
)
os.environ.setdefault("E2EPOOL_REDIS_URL", "redis://localhost:6381/1")
os.environ.setdefault("E2EPOOL_INVENTORY_PATH", "inventory.example.yml")
os.environ.setdefault("E2EPOOL_FINALIZE_COOLDOWN_SECONDS", "5")

from e2epool.database import Base, get_db
from e2epool.dependencies import set_backends, set_inventory
from e2epool.inventory import Inventory, RunnerConfig
from e2epool.main import app

TEST_DATABASE_URL = os.environ["E2EPOOL_DATABASE_URL"]

engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all tables once per test session."""
    # Enable btree_gist for exclusion constraint support
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conn.commit()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """Provide a transactional DB session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    # Start a nested savepoint
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        nonlocal nested
        if transaction.nested and not transaction._parent.nested:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def db_session_factory():
    """Provide a factory for independent DB sessions (for locking tests)."""

    def _factory():
        return TestSessionLocal()

    return _factory


def _make_runner(
    runner_id="test-runner-01",
    backend="proxmox",
    token="test-token-01",
    **kwargs,
):
    defaults = {
        "runner_id": runner_id,
        "backend": backend,
        "token": token,
        "proxmox_host": "10.0.0.10",
        "proxmox_user": "root@pam",
        "proxmox_token_name": "e2epool",
        "proxmox_token_value": "test-token",
        "proxmox_node": "pve1",
        "proxmox_vmid": 100,
        "ci_runner_id": 42,
    }
    if backend == "bare_metal":
        defaults.update(
            {
                "reset_cmd": "/opt/e2e/reset.sh",
                "cleanup_cmd": "/opt/e2e/cleanup.sh",
                "readiness_cmd": "/opt/e2e/check-ready.sh",
                "proxmox_host": None,
                "proxmox_user": None,
                "proxmox_token_name": None,
                "proxmox_token_value": None,
                "proxmox_node": None,
                "proxmox_vmid": None,
            }
        )
    defaults.update(kwargs)
    return RunnerConfig(**defaults)


@pytest.fixture
def mock_runner():
    return _make_runner()


@pytest.fixture
def mock_bare_metal_runner():
    return _make_runner(
        runner_id="test-bare-01",
        backend="bare_metal",
        token="test-token-bare-01",
    )


@pytest.fixture
def mock_inventory(mock_runner, mock_bare_metal_runner):
    inv = Inventory(
        {
            mock_runner.runner_id: mock_runner,
            mock_bare_metal_runner.runner_id: mock_bare_metal_runner,
        }
    )
    set_inventory(inv)
    return inv


@pytest.fixture
def mock_backend():
    backend = MagicMock()
    backend.create_checkpoint = MagicMock()
    backend.reset = MagicMock()
    backend.cleanup = MagicMock()
    backend.check_ready = MagicMock(return_value=True)
    set_backends({"proxmox": backend, "bare_metal": backend})
    return backend


@pytest.fixture
def client(db, mock_inventory, mock_backend):
    """TestClient with overridden DB dependency."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with patch("e2epool.main.reconcile_on_startup"):
        yield TestClient(app)
    app.dependency_overrides.clear()
