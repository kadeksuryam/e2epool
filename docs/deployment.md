# Production Deployment Guide

This guide covers deploying e2epool in production. It assumes familiarity with the [architecture overview](./architecture.md) and the project [README](../README.md).

---

## 1. Prerequisites

### Hardware

| Component | Minimum | Notes |
|-----------|---------|-------|
| Controller host | 1-2 vCPUs, 512 MB-1 GB RAM | IO-bound service, not compute-heavy |
| PostgreSQL | 1-2 vCPUs, 1-2 GB RAM, 5-10 GB disk | Small working set; grows slowly |
| Redis | Minimal (256 MB RAM) | Celery broker + result backend |

### Software

- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- Docker & Docker Compose (if using containerized deployment)

### Network

- Controller must reach: PostgreSQL, Redis, Proxmox API (port 8006), GitLab API
- Runner hosts must reach: controller WebSocket endpoint (egress only)
- If controller is outside the runner network, use WireGuard VPN for connectivity

---

## 2. Controller Deployment

### Option A: Docker Compose

**1. Create a production `docker-compose.prod.yml`:**

```yaml
services:
  db:
    image: postgres:14
    restart: always
    environment:
      POSTGRES_DB: e2epool
      POSTGRES_USER: e2epool
      POSTGRES_PASSWORD: "${DB_PASSWORD}"
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U e2epool"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7
    restart: always
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    restart: always
    command: uvicorn e2epool.main:app --host 0.0.0.0 --port 8080
    ports:
      - "8080:8080"
    env_file: .env
    environment:
      E2EPOOL_DATABASE_URL: "postgresql://e2epool:${DB_PASSWORD}@db:5432/e2epool"
      E2EPOOL_REDIS_URL: redis://redis:6379/0
      E2EPOOL_API_BASE_URL: http://app:8080
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./inventory.yml:/app/inventory.yml:ro
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"]
      interval: 10s
      timeout: 5s
      retries: 3

  celery-worker:
    build: .
    restart: always
    command: celery -A e2epool.tasks.celery_app worker --loglevel=info
    env_file: .env
    environment:
      E2EPOOL_DATABASE_URL: "postgresql://e2epool:${DB_PASSWORD}@db:5432/e2epool"
      E2EPOOL_REDIS_URL: redis://redis:6379/0
      E2EPOOL_API_BASE_URL: http://app:8080
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./inventory.yml:/app/inventory.yml:ro

  celery-beat:
    build: .
    restart: always
    command: celery -A e2epool.tasks.celery_app beat --loglevel=info
    env_file: .env
    environment:
      E2EPOOL_DATABASE_URL: "postgresql://e2epool:${DB_PASSWORD}@db:5432/e2epool"
      E2EPOOL_REDIS_URL: redis://redis:6379/0
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./inventory.yml:/app/inventory.yml:ro

volumes:
  pgdata:
```

Note: compared to the dev `docker-compose.yml`, production removes `--reload`, removes source volume mounts (`.:/app`), binds DB/Redis to localhost only, and uses a secret for the DB password.

**3. Build and start:**

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

**4. Run migrations:**

```bash
docker compose -f docker-compose.prod.yml exec app alembic upgrade head
```

### Option B: systemd

**1. Install e2epool:**

```bash
git clone <your-e2epool-repo-url> /opt/e2epool
cd /opt/e2epool
python3 -m venv /opt/e2epool/venv
/opt/e2epool/venv/bin/pip install .
```

**2. Create environment file:**

```bash
# /etc/e2epool/controller.env
E2EPOOL_DATABASE_URL=postgresql://e2epool:<password>@localhost:5432/e2epool
E2EPOOL_REDIS_URL=redis://localhost:6379/0
E2EPOOL_INVENTORY_PATH=/etc/e2epool/inventory.yml
E2EPOOL_API_BASE_URL=http://127.0.0.1:8080
```

**3. Create systemd units:**

API server (`/etc/systemd/system/e2epool-api.service`):

```ini
[Unit]
Description=e2epool API Server
After=network-online.target postgresql.service redis.service

[Service]
Type=simple
EnvironmentFile=/etc/e2epool/controller.env
ExecStart=/opt/e2epool/venv/bin/uvicorn e2epool.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10
User=e2epool

[Install]
WantedBy=multi-user.target
```

Celery worker (`/etc/systemd/system/e2epool-worker.service`):

```ini
[Unit]
Description=e2epool Celery Worker
After=network-online.target postgresql.service redis.service

[Service]
Type=simple
EnvironmentFile=/etc/e2epool/controller.env
ExecStart=/opt/e2epool/venv/bin/celery -A e2epool.tasks.celery_app worker --loglevel=info
Restart=always
RestartSec=10
User=e2epool

[Install]
WantedBy=multi-user.target
```

Celery beat (`/etc/systemd/system/e2epool-beat.service`):

```ini
[Unit]
Description=e2epool Celery Beat
After=network-online.target postgresql.service redis.service

[Service]
Type=simple
EnvironmentFile=/etc/e2epool/controller.env
ExecStart=/opt/e2epool/venv/bin/celery -A e2epool.tasks.celery_app beat --loglevel=info
Restart=always
RestartSec=10
User=e2epool

[Install]
WantedBy=multi-user.target
```

**4. Enable and start:**

```bash
systemctl daemon-reload
systemctl enable --now e2epool-api e2epool-worker e2epool-beat
```

### Database Setup

If not using the Docker Compose `db` service, provision PostgreSQL manually:

```bash
sudo -u postgres createuser e2epool
sudo -u postgres createdb -O e2epool e2epool
sudo -u postgres psql -c "ALTER USER e2epool WITH PASSWORD '<password>';"
```

### Run Migrations

```bash
# Docker Compose
docker compose -f docker-compose.prod.yml exec app alembic upgrade head

# systemd
source /opt/e2epool/venv/bin/activate
export E2EPOOL_DATABASE_URL=postgresql://e2epool:<password>@localhost:5432/e2epool
alembic upgrade head
```

### Redis Setup

If not using the Docker Compose `redis` service:

```bash
# Debian/Ubuntu
apt install redis-server
systemctl enable --now redis-server

# Verify
redis-cli ping  # → PONG
```

---

## 3. Inventory Configuration

Create `inventory.yml` with your runner definitions. See `inventory.example.yml` for the full template.

### Proxmox Runner

```yaml
runners:
  - runner_id: runner-proxmox-01
    backend: proxmox
    token: "<strong-random-token>"
    ci_adapter: gitlab
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
    proxmox_token_name: "e2epool"
    proxmox_token_value: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    proxmox_node: "pve1"
    proxmox_vmid: 100
    cleanup_cmd: "sudo /opt/e2e/cleanup.sh"
    gitlab_url: "https://gitlab.example.com"
    gitlab_token: "glpat-xxxxxxxxxxxxxxxxxxxx"
    gitlab_runner_id: 42
    tags:
      - e2e
      - proxmox
```

### Bare-Metal Runner

```yaml
  - runner_id: runner-bare-01
    backend: bare_metal
    token: "<strong-random-token>"
    ci_adapter: gitlab
    reset_cmd: "sudo /opt/e2e/reset.sh"
    cleanup_cmd: "sudo /opt/e2e/cleanup.sh"
    readiness_cmd: "/opt/e2e/check-ready.sh"
    gitlab_url: "https://gitlab.example.com"
    gitlab_token: "glpat-xxxxxxxxxxxxxxxxxxxx"
    gitlab_runner_id: 43
    tags:
      - e2e
      - bare-metal
```

### Required Fields by Backend

| Field | Proxmox | Bare Metal |
|-------|---------|------------|
| `runner_id` | required | required |
| `backend` | `"proxmox"` | `"bare_metal"` |
| `token` | required | required |
| `ci_adapter` | required | required |
| `reset_cmd` | optional | **required** |
| `cleanup_cmd` | optional | optional |
| `readiness_cmd` | optional | optional |
| `proxmox_host` | required | not used |
| `proxmox_user` | required | not used |
| `proxmox_token_name` | required | not used |
| `proxmox_token_value` | required | not used |
| `proxmox_node` | required | not used |
| `proxmox_vmid` | required | not used |
| `gitlab_url` | required | required |
| `gitlab_token` | required | required |
| `gitlab_runner_id` | optional (enables pause/unpause) | optional |
| `tags` | optional | optional |

### Token Generation

Generate a cryptographically random token for each runner:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use the same token value in both `inventory.yml` (on the controller) and `agent.yml` (on the runner host).

> **Important:** The inventory is loaded once at startup. After editing `inventory.yml`, restart the API server. Celery workers pick up inventory changes automatically on each task.

---

## 4. Agent Deployment on Runner Hosts

The agent is a lightweight daemon running on each CI runner host. It maintains a persistent outbound WebSocket connection to the controller, so runners need only egress — no ingress firewall rules.

### Install e2epool

The package is not published to PyPI — install from the git repository:

```bash
# Prerequisites
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git

# Clone and install
git clone <your-e2epool-repo-url> /opt/e2epool
cd /opt/e2epool
python3 -m venv /opt/e2epool/venv
/opt/e2epool/venv/bin/pip install .

# Symlink the CLI so systemd and shell can find it
sudo ln -s /opt/e2epool/venv/bin/e2epool /usr/local/bin/e2epool

# Verify
e2epool --help
```

### Create Agent Config

```yaml
# /etc/e2epool/agent.yml
controller_url: "ws://controller.internal:8080/ws/agent"
runner_id: "runner-proxmox-01"
token: "secret-token-for-this-runner"
socket_path: "/var/run/e2epool-agent.sock"
reconnect_max_delay: 60
heartbeat_interval: 30
```

```bash
sudo mkdir -p /etc/e2epool
sudo chmod 600 /etc/e2epool/agent.yml
```

All fields can be overridden with environment variables: `E2EPOOL_CONTROLLER_URL`, `E2EPOOL_RUNNER_ID`, `E2EPOOL_TOKEN`, `E2EPOOL_SOCKET_PATH`, `E2EPOOL_RECONNECT_MAX_DELAY`, `E2EPOOL_HEARTBEAT_INTERVAL`.

The config file path defaults to `/etc/e2epool/agent.yml` and can be overridden with `E2EPOOL_AGENT_CONFIG`.

### Linux: systemd Service

```bash
# Copy the unit file
sudo cp /opt/e2epool/systemd/e2epool-agent.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now e2epool-agent
```

The provided unit file (`systemd/e2epool-agent.service`):

```ini
[Unit]
Description=e2epool Agent
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/e2epool agent
Restart=always
RestartSec=10
Environment=E2EPOOL_AGENT_CONFIG=/etc/e2epool/agent.yml

[Install]
WantedBy=multi-user.target
```

### macOS: launchd Plist

```bash
sudo cp /opt/e2epool/launchd/com.e2epool.agent.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.e2epool.agent.plist
```

The provided plist logs to `/var/log/e2epool-agent.stdout.log` and `/var/log/e2epool-agent.stderr.log`.

### Verification

**Linux:**

```bash
systemctl status e2epool-agent
journalctl -u e2epool-agent -f
e2epool status --checkpoint job-test-1234567890-abcdef01
```

**macOS:**

```bash
sudo launchctl list | grep e2epool
tail -f /var/log/e2epool-agent.stdout.log
e2epool status --checkpoint job-test-1234567890-abcdef01
```

Exit codes: `0` = success, `1` = error, `2` = agent not running.

---

## 5. CI Integration

### GitLab CI (via agent)

Add to your `.gitlab-ci.yml`:

```yaml
.e2e_base: &e2e_base
  before_script:
    - export CHECKPOINT_NAME=$(e2epool create --job-id $CI_JOB_ID)
    - echo "Checkpoint created: $CHECKPOINT_NAME"

  after_script:
    # 1. Upload artifacts FIRST (before finalize can reset the runner)
    - cp -r test-results/ $CI_PROJECT_DIR/test-results/ 2>/dev/null || true
    # 2. Finalize via agent
    - e2epool finalize --checkpoint $CHECKPOINT_NAME --status $CI_JOB_STATUS || true

# Example job
e2e_tests:
  <<: *e2e_base
  tags: [e2e]
  script:
    - docker compose -f docker-compose.e2e.yml up -d
    - docker compose -f docker-compose.e2e.yml exec backend pytest tests/e2e
  artifacts:
    when: always
    paths:
      - test-results/
```

No auth headers, no `runner_id` in CI, no `jq` parsing. The token lives in the agent config on the runner host.

### Runner Configuration

Each runner **must** have `concurrent = 1` in its CI runner config:

```toml
# /etc/gitlab-runner/config.toml
concurrent = 1

[[runners]]
  name = "e2e-01"
  url = "https://gitlab.example.com"
  token = "RUNNER_TOKEN"
  executor = "shell"
  tag_list = ["e2e"]
```

---

## 6. Network & Firewall

### Port Requirements

| Source | Destination | Port | Protocol | Purpose |
|--------|------------|------|----------|---------|
| Runner agent | Controller | 8080 (or 443 via proxy) | TCP (WebSocket) | Agent connection (egress only) |
| Controller | PostgreSQL | 5432 | TCP | Database |
| Controller | Redis | 6379 | TCP | Celery broker |
| Controller | Proxmox API | 8006 | TCP/HTTPS | VM snapshot management |
| Controller | GitLab API | 443 | TCP/HTTPS | Job status polling, runner pause/unpause |
| Operator | Controller | 8080 (or 443) | TCP/HTTP(S) | Health checks, manual API calls |

### WebSocket Agent Networking

The agent opens an **outbound** WebSocket connection from the runner to the controller. This means:

- Runners need **only egress** to the controller — no ingress firewall rules required
- No pfSense NAT rules needed for runners
- Works through corporate firewalls that allow outbound HTTPS

### WireGuard VPN for Bare-Metal Runners

Bare-metal runners behind NAT (e.g., Mac Minis in the office) connect via WireGuard VPN:

1. Configure WireGuard on the office router or directly on each Mac Mini
2. Establish a tunnel to the controller network (e.g., via pfSense)
3. The agent's `controller_url` uses the controller's VPN-reachable address
4. Only the VPN subnet should be routable between sites

---

## 7. TLS

### Reverse Proxy (nginx)

Place nginx in front of the API for HTTPS/WSS termination:

```nginx
upstream e2epool_api {
    server 127.0.0.1:8080;
}

server {
    listen 443 ssl;
    server_name e2epool.example.com;

    ssl_certificate     /etc/ssl/certs/e2epool.crt;
    ssl_certificate_key /etc/ssl/private/e2epool.key;

    # Block internal-only endpoints from external access (no auth, used by Celery workers)
    location /internal/ {
        return 403;
    }

    location / {
        proxy_pass http://e2epool_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support for /ws/agent
    location /ws/ {
        proxy_pass http://e2epool_api;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

### Agent Configuration with TLS

Update the agent config to use `wss://`:

```yaml
# /etc/e2epool/agent.yml
controller_url: "wss://e2epool.example.com/ws/agent"
```

---

## 8. Security Checklist

- [ ] **Runner tokens:** Use unique, cryptographically random tokens per runner. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
- [ ] **Token rotation:** Rotate runner tokens periodically. Update both `inventory.yml` (controller) and `agent.yml` (runner), then restart both
- [ ] **Database credentials:** Use a strong password for PostgreSQL. Store in environment variables or a secrets manager, never in version control
- [ ] **Proxmox API token:** Create a dedicated PVE API user (`e2epool@pve`) with privileges limited to `VM.Snapshot`, `VM.Snapshot.Rollback`, `VM.PowerMgmt` on pool VM IDs only
- [ ] **GitLab API tokens:** Use separate tokens with minimal scope — `read_api` for job polling, `manage_runner` for pause/unpause
- [ ] **Agent config permissions:** Restrict `/etc/e2epool/agent.yml` to the service user: `chmod 0600 /etc/e2epool/agent.yml`
- [ ] **Controller env file permissions:** `chmod 0600 /etc/e2epool/controller.env`
- [ ] **IPC socket:** The agent creates the Unix socket with `0660` permissions — only authorized local processes can send commands
- [ ] **TLS:** Always use TLS (HTTPS/WSS) in production. Terminate at a reverse proxy or load balancer
- [ ] **Database TLS:** Require TLS for PostgreSQL connections from outside the local network (`?sslmode=require` in the connection string)
- [ ] **Network ACLs:** Controller API should be accessible only from runner subnets and operator machines. Database should be accessible only from controller instances

---

## 9. Configuration Reference

All controller settings are configured via `E2EPOOL_` prefixed environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `E2EPOOL_DATABASE_URL` | `postgresql://e2epool:e2epool@localhost:5432/e2epool` | PostgreSQL connection string |
| `E2EPOOL_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for Celery broker |
| `E2EPOOL_INVENTORY_PATH` | `inventory.yml` | Path to runner inventory YAML |
| `E2EPOOL_CHECKPOINT_TTL_SECONDS` | `1800` | GC threshold: checkpoints older than this get reset (30 min) |
| `E2EPOOL_GC_INTERVAL_SECONDS` | `60` | How often GC runs |
| `E2EPOOL_RECONCILE_INTERVAL_SECONDS` | `120` | How often stuck `finalize_queued` checkpoints are re-enqueued |
| `E2EPOOL_POLLER_INTERVAL_SECONDS` | `20` | How often the poller checks CI job statuses |
| `E2EPOOL_POLLER_MIN_AGE_SECONDS` | `120` | Skip polling checkpoints younger than this |
| `E2EPOOL_FINALIZE_COOLDOWN_SECONDS` | `5` | Minimum time between finalize and next create |
| `E2EPOOL_READINESS_TIMEOUT_SECONDS` | `120` | Max wait for runner agent readiness after reset |
| `E2EPOOL_READINESS_POLL_INTERVAL_SECONDS` | `5` | Interval between readiness polls |
| `E2EPOOL_API_BASE_URL` | `http://127.0.0.1:8080` | Internal API URL (for agent RPC from Celery workers) |
| `E2EPOOL_DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `E2EPOOL_DB_MAX_OVERFLOW` | `5` | Max connections above pool size |
| `E2EPOOL_DB_POOL_RECYCLE` | `1800` | Recycle DB connections after N seconds |
| `E2EPOOL_TASK_SOFT_TIME_LIMIT` | `300` | Celery soft time limit for finalize/GC tasks (seconds) |
| `E2EPOOL_TASK_HARD_TIME_LIMIT` | `330` | Celery hard time limit for finalize/GC tasks (seconds) |
| `E2EPOOL_POLLER_SOFT_TIME_LIMIT` | `120` | Poller task soft time limit (seconds) |
| `E2EPOOL_POLLER_HARD_TIME_LIMIT` | `150` | Poller task hard time limit (seconds) |
| `E2EPOOL_WS_HEARTBEAT_INTERVAL` | `30` | WebSocket heartbeat interval (seconds) |
| `E2EPOOL_WS_HEARTBEAT_TIMEOUT` | `90` | WebSocket heartbeat timeout (seconds) |
| `E2EPOOL_HTTPX_TIMEOUT` | `30` | HTTP client timeout for GitLab API calls (seconds) |
| `E2EPOOL_QUERY_BATCH_SIZE` | `200` | Batch size for DB queries in periodic tasks |

### Agent Configuration

Agent settings are configured via `/etc/e2epool/agent.yml` or environment variables.

| YAML field | Env var | Default | Description |
|------------|---------|---------|-------------|
| `controller_url` | `E2EPOOL_CONTROLLER_URL` | `ws://localhost:8080/ws/agent` | Controller WebSocket URL |
| `runner_id` | `E2EPOOL_RUNNER_ID` | `""` | Runner identifier (must match inventory) |
| `token` | `E2EPOOL_TOKEN` | `""` | Authentication token (must match inventory) |
| `socket_path` | `E2EPOOL_SOCKET_PATH` | `/var/run/e2epool-agent.sock` | Unix domain socket path for IPC |
| `reconnect_max_delay` | `E2EPOOL_RECONNECT_MAX_DELAY` | `60` | Max reconnect backoff delay (seconds) |
| `heartbeat_interval` | `E2EPOOL_HEARTBEAT_INTERVAL` | `30` | WebSocket heartbeat interval (seconds) |

Config file path: set via `E2EPOOL_AGENT_CONFIG` (default: `/etc/e2epool/agent.yml`). Environment variables override YAML values.

### Production-Recommended Values

```bash
# /etc/e2epool/controller.env (production)
E2EPOOL_DATABASE_URL=postgresql://e2epool:<password>@db.internal:5432/e2epool?sslmode=require
E2EPOOL_REDIS_URL=redis://redis.internal:6379/0
E2EPOOL_INVENTORY_PATH=/etc/e2epool/inventory.yml
E2EPOOL_API_BASE_URL=http://127.0.0.1:8080
E2EPOOL_CHECKPOINT_TTL_SECONDS=1800
E2EPOOL_DB_POOL_SIZE=10
E2EPOOL_DB_POOL_RECYCLE=1800
E2EPOOL_WS_HEARTBEAT_INTERVAL=30
E2EPOOL_WS_HEARTBEAT_TIMEOUT=90
```

---

## 10. Health Checks & Monitoring

### `/healthz` Endpoint

The controller exposes `GET /healthz` which checks PostgreSQL connectivity:

```bash
curl http://localhost:8080/healthz
# → {"status": "ok"}          (200)
# → {"status": "unhealthy"}   (503, if DB is unreachable)
```

Use this for load balancer health checks, monitoring scripts, and container orchestrator probes.

### Log Aggregation

All controller components log structured output to stdout. In production:

- **Docker Compose:** Use `docker compose logs -f` or configure a logging driver (e.g., `json-file`, `fluentd`, `gelf`)
- **systemd:** Logs go to the journal — view with `journalctl -u e2epool-api -u e2epool-worker -u e2epool-beat -f`

### Key Metrics to Watch

| Metric | Source | What it tells you |
|--------|--------|-------------------|
| Checkpoint create duration | Controller logs | Storage/API performance |
| Reset/rollback duration | Controller logs | Proxmox or reset script performance |
| Readiness check duration | Controller logs | Time until runner is accepting jobs again |
| Finalize source distribution | Controller logs | How often the poller is the fallback vs. the hook |
| Pending checkpoint count | GC logs (periodic) | Whether checkpoints are being cleaned up |
| Stale checkpoint age | GC logs | Whether GC is keeping up |
| Agent WebSocket reconnects | Agent logs | Network stability |

### Alerting Triggers

Set up alerts (cron + curl, external monitor, or Prometheus) for:

- `/healthz` returns non-200 (controller or DB down)
- Stale checkpoints older than TTL exist
- Runner readiness timeout after reset (runner stays paused)
- Celery worker or beat process not running
- PostgreSQL connection pool exhaustion

---

## 11. Backup & Recovery

### PostgreSQL Backup

The database is the single source of truth. Back up regularly:

```bash
# Daily pg_dump (add to cron)
pg_dump -h localhost -U e2epool e2epool | gzip > /backups/e2epool_$(date +%Y%m%d_%H%M%S).sql.gz

# Retention: keep at least 7 days
find /backups -name "e2epool_*.sql.gz" -mtime +7 -delete
```

For managed databases (AWS RDS, Hetzner Cloud), enable automated backups.

### Recovery

**Restore from backup:**

```bash
gunzip -c /backups/e2epool_20260213_030000.sql.gz | psql -h localhost -U e2epool e2epool
```

### Controller Down

If the controller goes down:

1. **CI jobs fail at the `before_script`** (cannot create checkpoints) — no E2E tests run, but no damage occurs
2. **Active checkpoints are safe** — their state is in PostgreSQL
3. **On restart**, the controller runs `reconcile_on_startup()` which scans for checkpoints stuck in `finalize_queued` state and re-enqueues them via Celery. Additionally, the periodic reconcile task (every 120s) catches any that get stuck during normal operation without requiring a restart.
4. **GC catches stale checkpoints** — any `created` checkpoints older than TTL (30 min) are automatically reset

### Agent Down

If the agent on a runner host goes down:

1. The systemd/launchd service auto-restarts it (`Restart=always` / `KeepAlive=true`)
2. The agent reconnects with exponential backoff (1s to 60s max)
3. CI jobs using `e2epool create` fail with exit code 2 until the agent reconnects
4. The job status poller still detects job completion via the CI API as a fallback

---

## 12. Operational Runbook

| Scenario | Symptoms | Remediation |
|----------|----------|-------------|
| **Failed checkpoint create** | Job fails at `before_script` | Check controller logs. Proxmox: investigate PVE API / storage IO. Bare-metal: check DB health. |
| **Runner never calls finalize** | Checkpoint stays in `created` state | Job status poller detects completion within ~20s. If both fail, GC catches within 30 min TTL. |
| **Failed finalize (success path)** | Snapshot not deleted | Controller retries. Proxmox: manual `qm delsnapshot <vmid> <snap>`. |
| **Stale checkpoints accumulating** | GC logs show repeated resets | Investigate flaky runners or network issues between controller and runners. |
| **Runner readiness timeout** | Runner stays paused in CI | SSH into runner, check services. Manually unpause via CI system after fix. |
| **Storage pool > 70%** (Proxmox) | Proxmox alerts | Check for stale snapshots: `qm listsnapshot <vmid>`. Run manual GC or reduce pool. |
| **Controller instance down** | `/healthz` fails | Restart the instance. It reconnects to the shared DB and reconciles on startup. |
| **All controllers down** | No E2E tests run | Start any instance — it reconciles from the shared DB. |
| **Database down** | All controllers fail | Restore DB from backup. Checkpoint state is recoverable. |
| **VPN tunnel down** | Bare-metal jobs fail | Check WireGuard status on pfSense and office router. Proxmox runners are unaffected. |
| **`reset_cmd` fails** (bare-metal) | Runner paused, alert fired | SSH into Mac Mini. Manually run reset script or fix the environment. Unpause runner. |
| **Agent disconnected** | Agent logs show reconnect attempts | Check network connectivity. Agent auto-reconnects with backoff. |
| **Redis down** | Celery tasks not processing | Restart Redis. On recovery, periodic reconcile task (every 120s) and `reconcile_on_startup()` pick up stuck finalize tasks. |

---

## 13. Upgrading

### Upgrade Procedure

1. **Pull the latest code:**

   ```bash
   # systemd
   cd /opt/e2epool && git pull
   /opt/e2epool/venv/bin/pip install .

   # Docker
   cd /opt/e2epool && git pull
   docker compose -f docker-compose.prod.yml build
   ```

2. **Run database migrations:**

   ```bash
   # Docker
   docker compose -f docker-compose.prod.yml exec app alembic upgrade head

   # systemd
   source /opt/e2epool/venv/bin/activate
   alembic upgrade head
   ```

3. **Restart services in order:**

   ```bash
   # Docker Compose (restarts all)
   docker compose -f docker-compose.prod.yml up -d

   # systemd (restart in order: beat → worker → api)
   systemctl restart e2epool-beat
   systemctl restart e2epool-worker
   systemctl restart e2epool-api
   ```

   Restart order rationale: stop beat first (no new periodic tasks), then worker (drains in-flight tasks), then api last (shortest downtime for incoming requests).

4. **Verify:**

   ```bash
   curl http://localhost:8080/healthz
   systemctl status e2epool-api e2epool-worker e2epool-beat  # systemd
   docker compose -f docker-compose.prod.yml ps               # Docker
   ```

### Upgrading Agents

After upgrading the controller, upgrade agents on each runner host:

```bash
cd /opt/e2epool && git pull
/opt/e2epool/venv/bin/pip install .
sudo systemctl restart e2epool-agent     # Linux
sudo launchctl unload /Library/LaunchDaemons/com.e2epool.agent.plist && \
sudo launchctl load /Library/LaunchDaemons/com.e2epool.agent.plist  # macOS
```

### Zero-Downtime Upgrade (multi-instance)

With multiple controller instances behind a load balancer:

1. Run migrations (forward-compatible)
2. Upgrade and restart instances one at a time
3. Verify `/healthz` on each instance before proceeding to the next
