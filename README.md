# e2epool

## Project Overview

**e2epool** is an E2E Test Pool Lifecycle Controller that manages checkpoint snapshots for CI/CD runners. It creates, finalizes, and garbage-collects VM snapshots (Proxmox) or bare-metal reset points so end-to-end test environments are automatically restored between pipeline runs.

## Stack

- **API:** FastAPI + Uvicorn (port 8080), HTTP + WebSocket
- **Agent:** WebSocket client daemon with Unix socket IPC (`e2epool` CLI)
- **Database:** PostgreSQL 14, SQLAlchemy 2.x, Alembic migrations
- **Task Queue:** Celery + Redis (broker & result backend)
- **Backends:** Proxmox (VM snapshots), Bare Metal (agent-based reset scripts)
- **CI Adapters:** GitLab (job polling, runner pause/unpause)
- **Python:** 3.11+

## Project Structure

```
e2epool/
  main.py              # FastAPI app entrypoint + lifespan (startup reconciliation)
  config.py            # Pydantic settings (env-prefixed E2EPOOL_*)
  database.py          # SQLAlchemy engine + session factories
  models.py            # Checkpoint, OperationLog (DB models)
  schemas.py           # Pydantic request/response schemas (incl. WSRequest/WSResponse)
  inventory.py         # RunnerConfig dataclass + YAML loader
  dependencies.py      # DI: inventory, backends, CI adapters, token auth, WS auth
  locking.py           # PostgreSQL advisory locks (runner-level)
  reconcile.py         # Startup reconciliation for stuck checkpoints
  protocol.py          # WebSocket message models (shared with agent)
  agent_config.py      # Agent configuration (YAML + env vars)
  agent.py             # WebSocket client daemon + IPC server
  ipc.py               # Unix domain socket IPC (length-prefixed JSON)
  cli.py               # Click CLI: agent, create, finalize, status
  __main__.py          # python -m e2epool entry point
  routers/
    health.py          # GET /healthz
    checkpoint.py      # POST /checkpoint/create, /checkpoint/finalize, GET /checkpoint/status/{name}
    runner.py          # GET /runner/readiness
    ws.py              # WS /ws/agent (WebSocket endpoint for agents)
  services/
    checkpoint_service.py  # Business logic: create_checkpoint, queue_finalize
    ws_handler.py      # WebSocket message dispatcher
    ws_manager.py      # WebSocket connection registry
  tasks/
    celery_app.py      # Celery app + beat schedule
    finalize.py        # do_finalize task (reset or cleanup)
    gc.py              # gc_stale_checkpoints periodic task
    poller.py          # poll_active_checkpoints periodic task
  backends/
    base.py            # BackendProtocol
    proxmox.py         # Proxmox VM snapshot backend
    bare_metal.py      # Bare metal agent-based backend
    agent_rpc.py       # Agent RPC helpers (sync HTTP to internal API)
  ci_adapters/
    base.py            # CIAdapterProtocol
    gitlab.py          # GitLab jobs/runners API adapter
systemd/
  e2epool-agent.service  # systemd unit for the agent daemon (Linux)
launchd/
  com.e2epool.agent.plist  # launchd plist for the agent daemon (macOS)
tests/                 # pytest test suite (205 tests)
alembic/               # Database migrations
```

## Prerequisites

- Python 3.11+
- PostgreSQL 14
- Redis 7
- Docker & Docker Compose (for containerized setup)

## Running Locally

### 1. Start infrastructure (DB + Redis)

```bash
docker compose up -d db redis
```

This starts PostgreSQL on `localhost:5434` and Redis on `localhost:6381`.

### 2. Set up Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Configure environment

Copy and edit the `.env` file:

```bash
# .env (defaults for local dev with docker compose)
E2EPOOL_DATABASE_URL=postgresql://e2epool:e2epool@localhost:5434/e2epool
E2EPOOL_REDIS_URL=redis://localhost:6381/0
E2EPOOL_INVENTORY_PATH=inventory.yml
```

All settings are configurable via `E2EPOOL_` prefixed env vars. See `e2epool/config.py` for the full list.

### 4. Run database migrations

```bash
alembic upgrade head
```

### 5. Create inventory file

Copy the example and fill in your runners:

```bash
cp inventory.example.yml inventory.yml
# Edit inventory.yml with your runner configuration
```

### 6. Start the API server

```bash
uvicorn e2epool.main:app --host 0.0.0.0 --port 8080 --reload
```

On startup, the app runs reconciliation to re-enqueue any stuck `finalize_queued` checkpoints.

### 7. Start the Celery worker

```bash
celery -A e2epool.tasks.celery_app worker --loglevel=info
```

The worker processes finalize tasks (reset/cleanup), GC, and poller jobs.

### 8. Start the Celery beat scheduler

```bash
celery -A e2epool.tasks.celery_app beat --loglevel=info
```

Beat triggers two periodic tasks:
- **poll-ci-jobs** every 20s (checks if CI jobs completed)
- **gc-stale-checkpoints** every 60s (resets checkpoints older than TTL)

## Running with Docker Compose (all-in-one)

```bash
docker compose up -d
```

This starts all 5 services: `db`, `redis`, `app`, `celery-worker`, `celery-beat`.

## WebSocket Agent

The e2epool agent is a lightweight daemon that runs on each CI runner host. It maintains a persistent **outbound** WebSocket connection to the controller, so runners only need egress — no ingress firewall rules required.

CLI commands (`e2epool create`, `e2epool finalize`, `e2epool status`) talk to the local agent via a Unix domain socket. The agent forwards requests over WebSocket to the controller and returns responses.

### How it works

```
CI job  ──>  e2epool create  ──>  Unix socket  ──>  Agent  ──>  WebSocket  ──>  Controller
                                                                                     │
CI job  <──  checkpoint name  <──  Unix socket  <──  Agent  <──  WebSocket  <────────┘
```

### Agent setup on a runner host

**1. Install e2epool**

```bash
pip install e2epool
```

**2. Create agent config**

```yaml
# /etc/e2epool/agent.yml
controller_url: "ws://controller:8080/ws/agent"
runner_id: "runner-proxmox-01"
token: "secret-token-for-this-runner"
socket_path: "/var/run/e2epool-agent.sock"
reconnect_max_delay: 60
heartbeat_interval: 30
```

All fields can be overridden with env vars: `E2EPOOL_CONTROLLER_URL`, `E2EPOOL_RUNNER_ID`, `E2EPOOL_TOKEN`, `E2EPOOL_SOCKET_PATH`, `E2EPOOL_RECONNECT_MAX_DELAY`, `E2EPOOL_HEARTBEAT_INTERVAL`.

**3. Enable the agent service**

**Linux (systemd):**

```bash
cp systemd/e2epool-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now e2epool-agent
```

**macOS (launchd):**

```bash
sudo cp launchd/com.e2epool.agent.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.e2epool.agent.plist
```

**4. Verify**

**Linux:**

```bash
systemctl status e2epool-agent
e2epool status --checkpoint job-test-1234567890-abcdef01
```

**macOS:**

```bash
sudo launchctl list | grep e2epool
e2epool status --checkpoint job-test-1234567890-abcdef01
```

### CLI commands

```bash
# Start the agent daemon (foreground, used by systemd)
e2epool agent

# Create a checkpoint (blocks until controller responds)
e2epool create --job-id $CI_JOB_ID
# Output: job-12345-1700000000-a1b2c3d4

# Finalize a checkpoint
e2epool finalize --checkpoint $CHECKPOINT_NAME --status success

# Query checkpoint status
e2epool status --checkpoint $CHECKPOINT_NAME
```

Exit codes: `0` = success, `1` = error, `2` = agent not running.

### CI integration (with agent)

```yaml
before_script:
  - export CHECKPOINT_NAME=$(e2epool create --job-id $CI_JOB_ID)
after_script:
  - e2epool finalize --checkpoint $CHECKPOINT_NAME --status $CI_JOB_STATUS
```

No auth headers, no runner_id in CI, no jq parsing. The token lives in the agent config on the runner host.

### Agent features

- **Reconnect with backoff**: exponential backoff (1s to 60s) on WebSocket disconnect
- **Heartbeat**: periodic ping to keep the connection alive (default 30s)
- **Graceful shutdown**: drains in-flight requests on SIGTERM/SIGINT
- **Socket permissions**: IPC socket created with `0660` permissions

## Adding a Worker (Runner)

### 1. Define the runner in `inventory.yml`

**Proxmox runner:**

```yaml
runners:
  - runner_id: runner-proxmox-01
    backend: proxmox
    token: "secret-token-for-this-runner"
    ci_adapter: gitlab
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
    proxmox_token_name: "e2epool"
    proxmox_token_value: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    proxmox_node: "pve1"
    proxmox_vmid: 100
    cleanup_cmd: "sudo /opt/e2e/cleanup.sh"   # optional: runs on success finalize
    gitlab_url: "https://gitlab.example.com"
    gitlab_token: "glpat-xxxxxxxxxxxxxxxxxxxx"
    gitlab_runner_id: 42             # GitLab runner ID (for pause/unpause)
    tags:
      - e2e
      - proxmox
```

**Bare metal runner:**

```yaml
runners:
  - runner_id: runner-bare-01
    backend: bare_metal
    token: "secret-token-for-this-runner"
    ci_adapter: gitlab
    reset_cmd: "sudo /opt/e2e/reset.sh"        # required for bare_metal
    cleanup_cmd: "sudo /opt/e2e/cleanup.sh"     # optional
    readiness_cmd: "/opt/e2e/check-ready.sh"    # optional (falls back to agent connectivity check)
    gitlab_url: "https://gitlab.example.com"
    gitlab_token: "glpat-xxxxxxxxxxxxxxxxxxxx"
    gitlab_runner_id: 43
    tags:
      - e2e
      - bare-metal
```

### 2. Required fields by backend

| Field | Proxmox | Bare Metal |
|-------|---------|------------|
| `runner_id` | required | required |
| `backend` | `"proxmox"` | `"bare_metal"` |
| `token` | required | required |
| `reset_cmd` | optional | **required** |
| `cleanup_cmd` | optional | optional |
| `readiness_cmd` | optional | optional |
| `proxmox_*` | required | not used |
| `gitlab_url/token` | required | required |
| `gitlab_runner_id` | optional (enables pause/unpause) | optional |

### 3. Restart the API server

The inventory is loaded once at startup. After editing `inventory.yml`, restart the API server (the Celery worker picks up inventory changes automatically on each task).

### 4. Use from CI

**Recommended: via agent** (requires agent running on runner host, egress only):

```yaml
before_script:
  - export CHECKPOINT_NAME=$(e2epool create --job-id $CI_JOB_ID)
after_script:
  - e2epool finalize --checkpoint $CHECKPOINT_NAME --status $CI_JOB_STATUS
```

**Alternative: direct HTTP** (requires runner network ingress to controller):

```bash
# Create a checkpoint
curl -X POST http://e2epool:8080/checkpoint/create \
  -H "Authorization: Bearer secret-token-for-this-runner" \
  -H "Content-Type: application/json" \
  -d '{"runner_id": "runner-proxmox-01", "job_id": "12345"}'

# Finalize (on job completion)
curl -X POST http://e2epool:8080/checkpoint/finalize \
  -H "Authorization: Bearer secret-token-for-this-runner" \
  -H "Content-Type: application/json" \
  -d '{"checkpoint_name": "job-12345-1700000000-a1b2c3d4", "status": "success"}'

# Check status
curl http://e2epool:8080/checkpoint/status/job-12345-1700000000-a1b2c3d4 \
  -H "Authorization: Bearer secret-token-for-this-runner"

# Health check (no auth)
curl http://e2epool:8080/healthz
```

## Configuration Reference

All settings via `E2EPOOL_` env vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://e2epool:e2epool@localhost:5432/e2epool` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL for Celery broker |
| `INVENTORY_PATH` | `inventory.yml` | Path to runner inventory YAML |
| `CHECKPOINT_TTL_SECONDS` | `1800` | GC threshold: checkpoints older than this get reset |
| `GC_INTERVAL_SECONDS` | `60` | How often GC runs |
| `POLLER_INTERVAL_SECONDS` | `20` | How often the poller checks CI job statuses |
| `POLLER_MIN_AGE_SECONDS` | `120` | Skip polling checkpoints younger than this |
| `FINALIZE_COOLDOWN_SECONDS` | `5` | Minimum time between finalize and next create |
| `READINESS_TIMEOUT_SECONDS` | `120` | Max wait for runner agent readiness |
| `READINESS_POLL_INTERVAL_SECONDS` | `5` | Interval between readiness polls |
| `API_BASE_URL` | `http://127.0.0.1:8080` | Internal API base URL (for agent RPC from Celery) |
| `DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `5` | Max connections above pool_size |
| `DB_POOL_RECYCLE` | `1800` | Recycle connections after N seconds |
| `TASK_SOFT_TIME_LIMIT` | `300` | Celery soft time limit (finalize, GC) |
| `TASK_HARD_TIME_LIMIT` | `330` | Celery hard time limit |
| `POLLER_SOFT_TIME_LIMIT` | `120` | Poller task soft time limit |
| `POLLER_HARD_TIME_LIMIT` | `150` | Poller task hard time limit |
| `WS_HEARTBEAT_INTERVAL` | `30` | WebSocket heartbeat interval (seconds) |
| `WS_HEARTBEAT_TIMEOUT` | `90` | WebSocket heartbeat timeout (seconds) |
| `HTTPX_TIMEOUT` | `30` | HTTP client timeout for GitLab API |
| `QUERY_BATCH_SIZE` | `200` | Batch size for DB queries in tasks |

## Development

### Lint and format

```bash
ruff check .
ruff format --check .
ruff format .           # auto-fix
```

### Run tests

Requires a running PostgreSQL (the test DB is separate):

```bash
# Start test DB
docker compose up -d db

# Create test database
psql -h localhost -p 5434 -U e2epool -c "CREATE DATABASE e2epool_test" 2>/dev/null || true

# Run tests
pytest tests/ -v
```

### Ruff config

- Line length: 88
- Target: Python 3.11
- Ignored: F403, F405
- Excluded: `alembic/versions/`

## Architecture Notes

### Checkpoint lifecycle

```
created  -->  finalize_queued  -->  reset    (failure/canceled: snapshot rollback)
                                -->  deleted  (success: snapshot cleanup)
created  -->  gc_reset                       (stale: GC auto-reset after TTL)
```

### Concurrency model

- **Per-runner advisory locks** prevent concurrent finalize/GC operations on the same runner
- **SELECT FOR UPDATE** on active checkpoint prevents concurrent creates for the same runner
- **Partial unique index** (`ix_one_active_checkpoint_per_runner`) is the DB-level safety net
- **Double-check pattern**: state is re-verified after acquiring the lock in finalize and GC tasks

### WebSocket agent architecture

```
Runner host                          Controller
┌──────────────────────┐             ┌──────────────────────────┐
│  CI job               │             │  FastAPI                  │
│    ↕ (IPC)            │             │                          │
│  e2epool CLI          │             │  WS /ws/agent            │
│    ↕ (Unix socket)    │             │    ↕                     │
│  Agent daemon    ─── WS (outbound) ──→  ws_handler             │
│    • reconnect        │             │    ↕                     │
│    • heartbeat        │             │  checkpoint_service      │
│    • request routing  │             │    ↕                     │
└──────────────────────┘             │  PostgreSQL + Celery     │
                                     └──────────────────────────┘
```

- Agent opens a **persistent outbound WebSocket** — runners need no ingress rules
- CLI communicates with the agent via **length-prefixed JSON over Unix domain socket**
- WebSocket auth uses `?runner_id=X&token=Y` query params (verified on connect)
- Agent reconnects with exponential backoff on disconnect
- Controller tracks connected agents via `WSManager` (in-memory registry)

### Reliability guarantees

- **task_acks_late + task_reject_on_worker_lost**: tasks survive worker crashes
- **Startup reconciliation**: re-enqueues stuck `finalize_queued` checkpoints
- **Unpause guarantee**: CI runners are always unpaused via `finally` blocks (inner + last-resort)
- **Broker failure tolerance**: if Redis is down when enqueuing, API returns 503 and reconciliation picks it up later
- **Batched queries**: all periodic tasks use `offset/limit` loops to avoid OOM on large datasets
- **Agent reconnect**: exponential backoff with jitter (1s → 60s max) on WS disconnect
- **Graceful agent shutdown**: drains in-flight IPC requests on SIGTERM, removes socket file
