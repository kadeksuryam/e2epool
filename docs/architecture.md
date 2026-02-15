# E2E Test Execution Pool — Architecture
---

## Revision

* **Author:** Kadek Surya Mahardika
* **Date:** 2026-02-15
* **Status:** Implemented

---

## 0. Executive summary

**TL;DR:** A stateless controller backed by PostgreSQL manages per-job checkpoint/reset for E2E runners across Proxmox VMs and bare-metal machines, with pluggable CI adapters (GitLab primary) and dual job-completion detection. No existing tool covers this exact combination — see section 1.3 for prior art.

This document describes the **implemented shared E2E test execution pool** with a **multi-backend runner lifecycle controller**. The pool is **project-agnostic** and **CI-system-agnostic**: any project (Voltavo, pvwebapp, mobile apps) can run E2E tests on any runner, orchestrated by any CI system (GitLab CI, GitHub Actions, Jenkins, etc.).

The controller is a **stateless service** backed by a shared PostgreSQL database. It can run **anywhere** (Proxmox VM, Hetzner Cloud, bare metal, Kubernetes) and supports **multiple instances** behind a load balancer for high availability. Each instance is identical — no leader election needed, since all operations are idempotent with distributed locking.

The controller has three extension points:

* **Runner backends** — how runners are checkpointed and reset:
  * `proxmox` — VMs on Hetzner dedicated servers. Per-job snapshot/rollback via the Proxmox REST API.
  * `bare_metal` — physical machines (e.g., Mac Minis in the office for mobile E2E). Reset via configurable scripts over SSH.

* **CI adapters** — how the controller interacts with the CI system:
  * `gitlab` — queries GitLab Jobs API, pauses/unpauses GitLab runners. (Primary implementation.)
  * `github_actions`, `jenkins`, etc. — future adapters. Same controller, different CI integration.

* **Deployment** — where the controller runs:
  * Proxmox VM, Hetzner Cloud VM, bare-metal server, Docker container, Kubernetes pod — anything with network access to runners, the CI API, and the shared database.
  * Multiple instances share state via PostgreSQL. No sticky sessions, no leader election.

The core mechanism is a **per-job checkpoint workflow**: before a job starts, the controller creates a checkpoint; on failure, it resets the runner; on success, it cleans up. Finalize is **asynchronous** (returns `202 Accepted`). A **job status poller** queries the CI system's API as a safety net, so finalize happens even if the runner's post-job hook never fires. This is outbound-only: the controller reaches out to the CI system, requiring no inbound ports or pfSense NAT rules.

Key outcomes:

* Per-job checkpoints with automatic reset on failure and cleanup on success
* Multi-backend: Proxmox VMs (datacenter) and bare-metal machines (office) managed by one controller
* CI-system-agnostic: pluggable CI adapters (GitLab CI as primary, extensible to others)
* Deployment-agnostic: stateless controller instances, deployable anywhere, multi-instance with shared PostgreSQL
* Asynchronous finalize with external job-completion detection as fallback
* Per-runner authentication scoped to individual runner IDs
* Runner readiness checks after reset before accepting new jobs
* Controller API with GC, metrics, and audit logging

---

## 1. Purpose and scope

### 1.1 Purpose

Provide a reproducible, operable architecture for running E2E tests using self-hosted runners and any CI system. The pool is shared across all projects (Voltavo, pvwebapp, mobile apps, etc.) — each project defines its own test script, while the infrastructure (runners, checkpoints, controller) is common. The controller is **project-agnostic, CI-agnostic, and deployment-agnostic**: it manages runner lifecycle via pluggable runner backends, interacts with the CI system via pluggable CI adapters, and can run as multiple stateless instances on any platform.

### 1.2 Scope

* Covers architecture, lifecycle, per-job checkpoint/reset, automated provisioning, storage planning, golden image lifecycle, and operational guidance.
* Includes API contract for the controller and CI integration patterns.
* Two runner backends: Proxmox VMs (datacenter) and bare-metal machines (office/on-prem).
* CI adapter interface with GitLab CI as the primary (reference) implementation. Extensible to GitHub Actions, Jenkins, etc.
* Controller deployment model: stateless instances with shared PostgreSQL, deployable on any platform.

### 1.3 Prior art and alternatives

This design was evaluated against existing solutions:

| Alternative | What it does | Why it's not sufficient |
|-------------|-------------|------------------------|
| **GitLab Runner Custom Executor** | Provides `prepare_exec`/`run_exec`/`cleanup_exec` hooks — checkpoint create/finalize could run inside them. | GitLab-specific; doesn't cover async reset, poller, GC, or multi-backend dispatch. The controller is still needed. Could be used as an optional tighter GitLab integration instead of `before_script`/`after_script`. |
| **Ephemeral runners** (destroy + re-clone per job) | Destroy the runner VM after each job, clone a fresh one from the golden template. No snapshot management. | Simpler model but slower turnaround (full clone + boot vs. rollback). Doesn't work for bare-metal runners. Viable for Proxmox-only setups where speed is not critical. |
| **Container-based executors** (Docker, Kubernetes) | Per-job isolation via containers, built into most CI systems. | E2E tests need full system access (Docker-in-Docker, host networking, real browsers, real databases). Container isolation adds limitations. Doesn't work for mobile E2E (iOS simulators need macOS host). |
| **Existing Proxmox CI tools** (cv4pve-pepper, Terraform providers) | VM management and provisioning for Proxmox. | Useful building blocks but none provide per-job snapshot/rollback integrated with CI lifecycle + multi-backend support. |

**What's novel:** Multi-backend runner lifecycle controller (Proxmox VMs + bare-metal machines), CI-agnostic adapter pattern, per-job checkpoint workflow with async finalize and dual completion detection.

**What uses existing patterns:** PostgreSQL advisory locks for distributed locking, stateless service with shared DB, CI runner tagging for job routing.

---

## 2. Requirements

### 2.1 Functional

* Create a checkpoint for the runner before the job starts (Proxmox snapshot for VMs, marker for bare-metal).
* If the job fails (non-zero exit / canceled), reset runner to that checkpoint automatically (rollback for VMs, reset script for bare-metal).
* If the job succeeds, clean up the checkpoint and optionally run a cleanup command.
* Handle runner crashes or CI cancellations and guarantee eventual cleanup or reset.
* Enforce **at most one active checkpoint per runner** — reject creation if one already exists.
* Upload test artifacts to the CI system **before** calling finalize, so reset does not destroy results.
* After reset and runner restart, verify readiness before marking it available for new jobs.

### 2.2 Non-functional

* Checkpoint creation should be predictable in time; the controller should emit metrics and timeouts.
* Garbage collection to remove stale checkpoints older than TTL (default: 30 minutes).
* Per-runner authentication: each runner token is scoped to its own runner ID.
* Rate limiting on controller API (max 1 active checkpoint per runner; creation rejected while one exists).
* Comprehensive audit logging of all lifecycle operations.

---

## 3. Concepts and invariants

### 3.1 General (all backends)

* **Runner backend:** each runner in the inventory declares a `backend` type that determines how checkpoints and resets work. Currently supported: `proxmox` (Proxmox VMs) and `bare_metal` (physical machines). The controller dispatches lifecycle operations to the appropriate backend handler. Adding a new backend requires only a new handler — no changes to the API or CI integration.
* **Per-job checkpoint:** a restore point created immediately before test execution. Named `job-<CI_JOB_ID>-<unix-ts>-<random-hex>` (e.g. `job-12345-1739451000-a1b2c3d4`). The implementation varies by backend (Proxmox snapshot vs. marker record).
* **Single-checkpoint invariant:** at most one active (non-finalized) checkpoint may exist per runner at any time. The controller rejects `POST /checkpoint/create` if one already exists. This prevents conflicting resets.
* **Concurrent=1 invariant:** each runner **must** be configured with `concurrent = 1` in its CI runner config. Enforced by the single-checkpoint invariant at the controller level.
* **Asynchronous finalize:** the runner calls `POST /checkpoint/finalize`, the controller persists the intent and returns `202 Accepted` immediately, then executes the reset asynchronously. This avoids the runner destroying itself mid-response (critical for Proxmox VMs where finalize stops the VM).
* **CI adapter:** a pluggable module the controller uses to interact with the CI system. It provides three operations: `get_job_status(job_id)`, `pause_runner(runner_id)`, and `unpause_runner(runner_id)`. The adapter is configured in the inventory (see section 6.2). Implementing a new CI adapter (e.g., for GitHub Actions) requires no changes to the controller core.
* **Dual job-completion detection:** finalize is triggered by three independent paths: (1) the runner's post-job hook (via `pre_build_script` checkpoint creation in GitLab runner config), (2) the controller's **job status poller** that queries the CI system's API via the CI adapter every 20s for jobs with active checkpoints (skips checkpoints < 2 min old), and (3) webhooks (`POST /webhooks/gitlab` or `POST /webhooks/github`) for real-time job completion events. Whichever detects completion first initiates finalize; the others are no-ops (idempotent). The poller path is purely outbound (controller -> CI system), requiring no inbound network exposure; the webhook path is inbound and optional.
* **Idempotency:** all controller endpoints must be idempotent; repeated finalize calls are safe.
* **Distributed locking:** per-runner lock (PostgreSQL advisory lock) to prevent concurrent conflicting operations, even across multiple controller instances.
* **Stateless controller:** each controller instance is stateless — all mutable state (checkpoint records, lock state, operation logs) lives in a **shared PostgreSQL database**. Instances can be started, stopped, or replaced without data loss. This makes the controller deployable anywhere: Proxmox VM, Hetzner Cloud VM, Docker container, Kubernetes pod, bare-metal server — anything with network access to the database, runners, and CI API.
* **Multi-instance:** multiple controller instances can run simultaneously behind a load balancer (or floating IP / DNS round-robin). There is no leader election. All instances serve the API, run the poller, and execute GC. Correctness is ensured by **distributed locking** (PostgreSQL advisory locks) and **idempotent operations**: if two instances both detect a job completion, only one acquires the lock and performs the reset; the other is a no-op.
* **Shared database:** PostgreSQL stores checkpoint records, operation logs, and distributed locks. Each controller instance connects to the same database. The database can run anywhere reachable by the controller instances (co-located, managed cloud service, etc.).
* **DB-backed runner registry:** the controller stores runner configuration in a PostgreSQL `runners` table. Runners are registered, updated, and deactivated via the admin API (`/api/runners`). The controller loads active runners from the database with a TTL cache. See section 6.2 for details.
* **Horizontal scaling (runners):** adding runner capacity means adding a new runner entry to the inventory. For Proxmox: provision a server, clone golden template, register. For bare-metal: install CI runner agent, connect via VPN, register. No changes to the controller code.
* **Horizontal scaling (controller):** adding controller capacity means starting another instance pointed at the same database. No coordination needed beyond shared DB access and consistent inventory.

### 3.2 Proxmox backend specifics

* **Snapshot tree semantics:** Proxmox snapshots form a tree. `qm rollback` discards all states *after* the target snapshot. The single-checkpoint invariant ensures the tree is always linear (base -> one job snapshot), avoiding corruption.
* **Full state isolation:** on failure, the VM is stopped, rolled back to the snapshot, and restarted. All filesystem and memory changes from the job are discarded.
* **Node-aware:** the controller uses the **Proxmox REST API** (not local `qm` CLI) to manage VMs across multiple physical Proxmox nodes from a single controller instance.

### 3.3 Bare-metal backend specifics

* **Script-based reset:** bare-metal runners (e.g., Mac Minis) don't have hypervisor snapshots. On failure, the controller runs a `reset_cmd` via SSH that cleans up the environment (kill processes, delete test data, reset simulators, etc.). On success, it runs a lighter `cleanup_cmd`.
* **Best-effort isolation:** unlike Proxmox rollback, script-based reset is not a full state restore. The reset script must be comprehensive enough to return the machine to a usable state. This is acceptable for environments like mobile E2E where the test footprint is smaller and more predictable.
* **No VM power cycle:** bare-metal runners stay running. The controller never stops or restarts them — only runs scripts and checks readiness.

### 3.4 Checkpoint state machine

Every checkpoint record transitions through these states. The state machine is the core invariant — all controller operations must respect it.

```
                         ┌──────────────────────────────────────┐
                         │                                      │
                         ▼                                      │
  POST /create ──► [created] ──► POST /finalize ──► [finalize_queued]
                     │                                   │
                     │ GC (TTL expired)                  │ async worker
                     │                                   │
                     ▼                                   ├── success ──► [deleted]
                   [gc_reset]                            │
                                                         └── failure ──► [reset]
```

| State | Meaning |
|-------|---------|
| `created` | Checkpoint exists, job is running. At most one per runner. |
| `finalize_queued` | Finalize intent recorded, async worker will process. |
| `reset` | Runner was rolled back / reset (failure path). Terminal. |
| `deleted` | Checkpoint cleaned up (success path). Terminal. |
| `gc_reset` | Stale checkpoint cleaned up by GC. Terminal. |

**Transitions:**
* `created → finalize_queued`: triggered by POST /finalize or by the job status poller.
* `finalize_queued → reset`: async worker performed rollback/reset (failure/canceled).
* `finalize_queued → deleted`: async worker cleaned up checkpoint (success).
* `created → gc_reset`: GC detected stale checkpoint (TTL expired), reset runner, cleaned up.
* No other transitions are valid. Duplicate finalize on a terminal state is a no-op (202).

**CI retry interaction:** if the CI system auto-retries a failed job on the same runner, the retry's `POST /create` will succeed only after the previous checkpoint reaches a terminal state. If the previous finalize is still in progress, the retry gets `409` and fails fast — this is correct behavior. The CI system will dispatch the retry to another available runner.

---

## 4. High-level architecture diagram

```
┌───────────────────┐
│   CI System       │
│   (GitLab, GitHub │
│    Actions, etc.) │
│                   │◄─── controller polls via CI adapter (outbound only)
│ ┌───────────────┐ │
│ │ CI Pipeline   │ │
│ └──────┬────────┘ │
│ ┌──────▼────────┐ │
│ │ Artifact Store│ │
│ └───────────────┘ │
└─────────┬─────────┘
          │ e2e / mobile tagged jobs
          │
          ▼

┌─────────────────────────────────────────────────────────────────────────────┐
│                        Controller Cluster (stateless)                       │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│  │ Controller #1    │  │ Controller #2    │  │ Controller #N    │          │
│  │ (Hetzner Cloud,  │  │ (Proxmox VM,    │  │ (anywhere)       │          │
│  │  Docker, K8s...) │  │  bare metal...) │  │                  │          │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘          │
│           └──────────────┬──────┘──────────────────────┘                    │
│                          │                                                  │
│                 ┌────────▼────────┐                                         │
│                 │ Shared Database │                                         │
│                 │ (PostgreSQL)    │                                         │
│                 └─────────────────┘                                         │
│  Load balancer / floating IP / DNS round-robin                              │
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │ checkpoint API, PVE API, SSH
                           │
          ┌────────────────┼─────────────────────────────────┐
          │                │                                  │
          ▼                ▼                                  ▼
┌──────────────────────────────────────────────┐
│  Node 1 - Runners + Infra (Hetzner AX41)    │
│                                              │
│  ┌────────────┐  ┌────────────┐             │
│  │  pfSense   │  │  Registry  │             │
│  │  (100)     │  │  (221)     │             │
│  └─────┬──────┘  └────────────┘             │
│        │ VPN                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  e2e-01  │  │  e2e-02  │  │  e2e-03  │  │
│  │  (211)   │  │  (212)   │  │  (213)   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│  Node 2 - Proxmox Expansion (future)        │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  e2e-04  │  │  e2e-05  │  │  e2e-06  │  │
│  │  (311)   │  │  (312)   │  │  (313)   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│  Office Site (via WireGuard VPN to pfSense)  │
│                                              │
│  ┌────────────┐  ┌────────────┐             │
│  │  mac-01    │  │  mac-02    │  bare-metal │
│  │  (Mac Mini) │  │  (Mac Mini) │  backend   │
│  └────────────┘  └────────────┘             │
│  iOS/Android E2E (Xcode, Appium, Detox)     │
└──────────────────────────────────────────────┘
```

**Data flow:**

```
Runners ──checkpoint API──► Controller (via LB / floating IP)    [HTTP, legacy]
Runners ──agent WS (outbound)──► Controller WS endpoint          [WebSocket, preferred]
Controller ──PVE REST API──► Proxmox VE (any node)               [proxmox backend]
Controller ──SSH──► Mac Minis (via VPN)                           [bare_metal backend]
Controller ──polls CI API──► CI System (fallback finalize)        [CI adapter]
Controller ──read/write──► Shared PostgreSQL                      [state store]
Proxmox runners ──pull images──► Registry (Node 1)
CI System ──artifacts──► Artifact Store
```

**Agent data flow (preferred — egress only, no ingress rules needed):**

```
CI job ──> e2epool CLI ──> Unix socket ──> Agent daemon ──> WebSocket (outbound) ──> Controller
```

**Diagram notes:**

* The **controller is not tied to any specific node**. It can run on a Proxmox VM, Hetzner Cloud VM, Docker container, Kubernetes pod, or bare-metal server — anywhere with network access to runners, PVE API, CI API, and the shared database.
* **Multiple controller instances** share state via PostgreSQL. Runners hit a stable endpoint (load balancer, floating IP, or DNS round-robin). Any instance can handle any request.
* **No leader election**: all instances run the API server, poller, and GC. Distributed locking (PostgreSQL advisory locks) ensures correctness. Duplicate operations are idempotent no-ops.
* The controller talks to the CI system via a **CI adapter** (pluggable). The primary adapter is for GitLab CI; adapters for GitHub Actions, Jenkins, etc. can be added without changing the controller core.
* All Proxmox runners (regardless of which physical node) pull from the same registry. Office Mac Minis don't need the registry (mobile builds are done differently).
* pfSense on Node 1 provides routing for the internal network **and** terminates the WireGuard VPN tunnel from the office. Nodes in the same datacenter use Hetzner vSwitch/VLAN.
* **Controller networking**: if the controller runs outside the internal network (e.g., Hetzner Cloud), it joins via WireGuard VPN to reach runners and PVE API. The CI API and database are reached directly (outbound).
* Adding capacity: Proxmox runners = new Hetzner server + clone template. Bare-metal runners = new machine + VPN client + register in inventory. Controller instances = start another instance pointed at the same DB.
* **WebSocket agent**: each runner host runs an `e2epool` agent daemon that maintains a persistent outbound WebSocket connection to the controller. CI jobs use `e2epool create`/`e2epool finalize` CLI commands that talk to the local agent via Unix domain socket. This eliminates the need for runners to have network ingress to the controller — only egress is required.
* Observability via controller logs + Proxmox built-in monitoring (no dedicated Prometheus/Loki for v1).

---

## 5. Per-job checkpoint workflow (detailed)

### 5.1 Goals

* Give each job a dedicated restore point taken immediately before test execution begins.
* Ensure a single authoritative actor (the controller) performs resets.
* Never allow the runner to destroy itself synchronously — finalize is always async.

### 5.2 Sequence (end-to-end)

1. **Pre-job hook** (e.g., GitLab `before_script`, GitHub Actions `pre` step): runner calls `POST /checkpoint/create` with `runner_id` and `job_id`. Controller validates the runner token, checks no active checkpoint exists, creates the checkpoint, and returns `checkpoint_name`.
2. **Job execution**: the runner executes the project's test script (e.g., `docker compose up` + `pytest`, Playwright, Detox, or any other tooling).
3. **Artifact upload** (post-job, first): runner explicitly uploads test artifacts to the CI system before calling finalize. This ensures artifacts survive a reset.
4. **Finalize request** (post-job, second): runner calls `POST /checkpoint/finalize` with `checkpoint_name`, `status` (`success|failure|canceled`), and optionally `source` (defaults to `hook`). `runner_id` is inferred from the Bearer token. Controller returns `202 Accepted` immediately.
5. **Fallback detection**: the controller's job status poller periodically queries the CI system's API (via the CI adapter) for every job with an active checkpoint. If the runner's finalize call never arrived (crash, network partition), the poller detects the job has completed and triggers finalize. Duplicate detections are idempotent no-ops.
6. **Controller async action** (dispatched by backend):

   | | **Proxmox backend** | **Bare-metal backend** |
   |--|---------------------|----------------------|
   | **Success** | Delete snapshot. Run `cleanup_cmd` if configured. | Run `cleanup_cmd` via SSH if configured. Delete checkpoint record. |
   | **Failure** | Pause runner → stop VM → rollback snapshot → start VM → delete snapshot → check readiness → unpause runner. | Pause runner → run `reset_cmd` via SSH → check readiness → unpause runner. |

7. **Readiness gate**: after reset, controller polls for runner readiness (SSH + service health check or custom `readiness_cmd`). Only after confirmed readiness does the controller unpause the runner via the CI adapter.
8. **Garbage collection**: background worker removes stale checkpoints older than TTL (default 30 minutes) and alerts on checkpoints that fail to resolve.

### 5.3 Naming & metadata

* Checkpoint name template: `job-<JOB_ID>-<unix-ts>-<random-hex>` (e.g. `job-12345-1739451000-a1b2c3d4`). The job ID comes from the CI system (e.g., `CI_JOB_ID` in GitLab, `GITHUB_RUN_ID` in GitHub Actions). The random suffix prevents collisions on retries.
* Controller records mapping `runner_id -> [name, job_id, created_at, state, finalize_status]` in the shared PostgreSQL database.

### 5.4 Timeouts & SLAs

Concrete values must be determined during the pilot phase by benchmarking actual operations under load. Starting points:

| Operation | Proxmox backend | Bare-metal backend |
|-----------|----------------|-------------------|
| Checkpoint creation | ~60s (depends on disk/storage) | ~1s (just a record) |
| Reset (failure) | ~3 min (stop + rollback + start) | ~30s-2 min (depends on `reset_cmd`) |
| Readiness check | ~2 min after VM start | ~10s (SSH + health check) |

Storage backend significantly affects Proxmox values: LVM-thin and ZFS are fast for snapshots; Ceph and NFS are slower. The pilot must measure p99 durations and set timeouts with a safety margin.

---

## 6. Controller API contract

### 6.1 API (HTTP + mutual TLS or per-runner Bearer token)

All endpoints require authentication. Each runner token is scoped to a specific `runner_id`; requests for a different runner are rejected with `403`.

**POST /checkpoint/create**

* Body: `{ "runner_id": "<string>", "job_id": "<string>", "caller": "<runner-name>" }` (`caller` is optional)
* Validation: `runner_id` must exist in inventory, `job_id` must be a non-empty string. Token must be scoped to the given `runner_id`.
* Response `201`: `{ "name": "job-12345-...", "runner_id": "...", "job_id": "...", "state": "created", "created_at": "..." }`
* Response `409`: active checkpoint already exists for this runner.
* Response `429`: cooldown period active (finalize completed too recently).
* Response `403`: token not authorized for this runner.

**POST /checkpoint/finalize**

* Body: `{ "checkpoint_name": "job-12345-...", "status": "success|failure|canceled", "source": "hook|poller" }` (`source` defaults to `"hook"`). The `runner_id` is inferred from the Bearer token.
* Validation: `checkpoint_name` must match `^job-[\w.\-]+-\d+-[0-9a-f]{8}$`.
* Response `202`: `{ "detail": "Finalize queued", "checkpoint_name": "job-12345-..." }`
* Duplicate finalize calls (already queued or terminal state) return `202` with `{ "detail": "Already finalized", "state": "..." }`.

**GET /checkpoint/status/{checkpoint_name}**

* Path parameter: `checkpoint_name`.
* Returns checkpoint state, timestamps, finalize status and source.

**GET /runner/readiness**

* `runner_id` is inferred from the Bearer token.
* Returns whether the runner is ready to accept jobs.

**GET /healthz**

* Returns 200 when controller is healthy.

**WS /ws/agent** (WebSocket — agent connection)

* Query params: `?runner_id=<string>&token=<string>` (validated on connect; close `4401` if invalid).
* Protocol: JSON messages using `WSRequest`/`WSResponse` envelope.
* Request: `{ "id": "<uuid>", "type": "create|finalize|status|ping", "payload": { ... } }`
* Response: `{ "id": "<uuid>", "status": "ok|error", "data": { ... }, "error": { "code": <int>, "detail": "<string>" } }`
* Message types:
  * `create` — payload `{ "job_id": "..." }`, returns `CheckpointResponse` data.
  * `finalize` — payload `{ "checkpoint_name": "...", "status": "success|failure|canceled", "source": "agent" }`, returns `{ "detail": "Finalize queued" }`.
  * `status` — payload `{ "checkpoint_name": "..." }`, returns `CheckpointResponse` data.
  * `ping` — returns `{ "pong": true }`.
* The controller tracks connected agents via an in-memory `WSManager` registry.
* The WebSocket endpoint calls the same service functions as the HTTP endpoints — no separate business logic.

Notes:
* `runner_id` is a string identifier (e.g., `"e2e-runner-01"`, `"mac-01"`). The runner reads its ID from the agent config file (`/etc/e2epool/agent.yml`).
* `job_id` is an opaque string from the CI system (e.g., `CI_JOB_ID` in GitLab, `GITHUB_RUN_ID` in GitHub Actions). The controller passes it through to the CI adapter for status queries.
* The implementation uses **FastAPI** with Pydantic models, **Celery** (Redis broker) for async tasks, and **SQLAlchemy ORM** with Alembic migrations.
* `finalize_source` values in the implementation: `hook` (runner post-job hook), `poller` (job status poller), `webhook` (GitLab/GitHub webhook), `agent` (WebSocket agent). Note: GC resets stale checkpoints directly (`created` → `gc_reset`) without going through finalize.

**Admin API** (requires `Authorization: Bearer <admin_token>`):

**POST /api/runners** — Register a new runner
* Body: runner configuration (runner_id, backend, proxmox_*, tags, etc.)
* Token is auto-generated (returned only on creation)
* Response `201`: runner details with token
* Response `409`: runner_id already exists and is active
* If a deactivated runner with the same runner_id exists, it is reactivated with new data and a new token.

**GET /api/runners** — List all runners
* Query param: `?include_inactive=true` to include deactivated runners
* Response `200`: list of runners (sensitive fields like token and proxmox_token_value are omitted)

**GET /api/runners/{runner_id}** — Get a specific runner
* Response `200`: runner details (sensitive fields omitted)
* Response `404`: runner not found

**DELETE /api/runners/{runner_id}** — Deactivate a runner (soft-delete)
* Sets `is_active=False`
* Response `200`: deactivated runner
* Response `404`: runner not found

**Webhook endpoints** (for real-time job completion detection):

**POST /webhooks/gitlab** — GitLab job completion webhook
* Header: `X-Gitlab-Token` (validated against `E2EPOOL_GITLAB_WEBHOOK_SECRET`)
* Body: GitLab build event JSON (`object_kind: "build"`)
* Triggers finalize with `source="webhook"` when job reaches terminal state
* Response `200`: `{"ok": true}`

**POST /webhooks/github** — GitHub Actions workflow_job completion webhook
* Header: `X-Hub-Signature-256` (HMAC-SHA256 signature validated against `E2EPOOL_GITHUB_WEBHOOK_SECRET`)
* Body: GitHub `workflow_job` event JSON
* Triggers finalize with `source="webhook"` when job completes
* Response `200`: `{"ok": true}`

**Internal API** (controller-to-agent RPC):

**POST /internal/agent/{runner_id}/exec** — Execute a command on a connected agent
* Body: `{"cmd": "<string>", "timeout": <float>}`
* Response `200`: `{"exit_code": <int>, "stdout": "<string>", "stderr": "<string>"}`
* Response `404`: agent not connected

**GET /internal/agent/{runner_id}/connected** — Check if an agent is connected
* Response `200`: `{"connected": <bool>}`

### 6.2 Runner registry

Runner configuration is stored in the PostgreSQL `runners` table. Runners are registered, updated, and deactivated via the **admin API** (`/api/runners`). The controller loads active runners from the database with a **TTL cache** (default: 5 minutes). All controller instances share the same database — no file synchronization needed.

See section 6.3 for the `runners` table schema and section 6.1 for the admin API endpoints.

### 6.3 Database schema

The shared PostgreSQL database stores checkpoint records, operation logs, and runner registry. All controller instances read and write to this schema.

```sql
CREATE TABLE runners (
    id              SERIAL PRIMARY KEY,
    runner_id       VARCHAR(255) NOT NULL,
    backend         VARCHAR(50) NOT NULL CHECK (backend IN ('proxmox', 'bare_metal')),
    token           VARCHAR(255) NOT NULL UNIQUE,
    proxmox_host    VARCHAR(255),
    proxmox_user    VARCHAR(255),
    proxmox_token_name VARCHAR(255),
    proxmox_token_value VARCHAR(255),
    proxmox_node    VARCHAR(255),
    proxmox_vmid    INTEGER,
    reset_cmd       TEXT,
    cleanup_cmd     TEXT,
    readiness_cmd   TEXT,
    gitlab_runner_id INTEGER,
    tags            TEXT,  -- JSON array stored as text
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- Unique constraint on runner_id (globally unique — reactivation overwrites the existing row)
CREATE UNIQUE INDEX ix_runners_runner_id ON runners (runner_id);
-- Unique constraint on token
CREATE UNIQUE INDEX ix_runners_token ON runners (token);

CREATE TABLE checkpoints (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    runner_id       VARCHAR(255) NOT NULL,
    job_id          VARCHAR(255) NOT NULL,
    state           VARCHAR(50) NOT NULL CHECK (state IN ('created','finalize_queued','reset','deleted','gc_reset')),
    finalize_status VARCHAR(50) CHECK (finalize_status IS NULL OR finalize_status IN ('success','failure','canceled')),
    finalize_source VARCHAR(50),
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    finalized_at    TIMESTAMP
);

-- Partial unique index: at most one active checkpoint per runner.
CREATE UNIQUE INDEX ix_one_active_checkpoint_per_runner ON checkpoints (runner_id)
    WHERE state IN ('created', 'finalize_queued');
-- Partial index for GC: efficiently find stale 'created' checkpoints.
CREATE INDEX ix_checkpoints_gc ON checkpoints (created_at)
    WHERE state = 'created';

CREATE TABLE operation_logs (
    id              SERIAL PRIMARY KEY,
    checkpoint_id   INTEGER NOT NULL REFERENCES checkpoints(id),
    runner_id       VARCHAR(255) NOT NULL,
    operation       VARCHAR(100) NOT NULL,  -- create, queue_finalize, finalize, gc
    backend         VARCHAR(50),            -- proxmox, bare_metal (nullable for non-backend ops)
    detail          TEXT,                   -- human-readable description
    result          VARCHAR(50),            -- ok, error
    started_at      TIMESTAMP NOT NULL DEFAULT now(),
    finished_at     TIMESTAMP,
    duration_ms     INTEGER
);
```

**Notes:**
* The partial unique index `ix_one_active_checkpoint_per_runner` enforces the single-checkpoint invariant at the database level — it prevents two rows with the same `runner_id` from existing in an active state simultaneously.
* The `ix_checkpoints_gc` partial index allows the GC worker to efficiently find stale checkpoints.
* The `operation_logs` table provides the structured audit trail referenced in sections 12 and 14.
* `finalize_source` values: `hook` (runner post-job hook), `poller` (job status poller), `gc` (garbage collection).
* The checkpoint column is named `name` (not `checkpoint`) to avoid ambiguity with the table name.
* `detail` is `TEXT` rather than `JSONB` — sufficient for audit logging in v1. Can be migrated to `JSONB` if structured querying is needed later.

### 6.4 Edge cases

* **Controller crash during finalize**: on startup, the controller's `reconcile_on_startup()` function scans for checkpoints in `finalize_queued` state and re-enqueues them via `do_finalize.delay()`. Finalize is idempotent.
* **Runner never calls finalize (crash, network partition)**: job status poller detects the job has completed via the CI adapter and triggers finalize. If both fail, GC catches it within TTL (30 min).
* **Checkpoint creation fails**: controller returns error to runner; job fails at the pre-job hook. For bare-metal (marker only), this is unlikely.
* **Runner readiness timeout after reset**: controller alerts, leaves runner paused in the CI system. Operator must investigate manually.
* **Concurrent create rejected**: if a second job is dispatched to the same runner (misconfiguration), controller returns `409` and the job fails fast. This signals that `concurrent` is misconfigured.
* **VPN tunnel down (bare-metal)**: controller can't SSH to office runners. Reset fails, runner stays paused. Poller still works (outbound to CI system). VPN auto-reconnect should recover within minutes.
* **Bare-metal reset_cmd fails**: runner is in unknown state. Controller pauses runner, alerts operator. Manual SSH intervention required.
* **Startup reconciliation**: on startup, the controller runs `reconcile_on_startup()` which scans the shared DB for checkpoints in `finalize_queued` state and re-dispatches them via `do_finalize.delay()`. This handles the case where the controller (or Celery worker) crashed mid-finalize. For v1, reconciliation only checks DB state. Future enhancement: for Proxmox runners, compare DB records against actual Proxmox snapshots (via PVE API) — orphaned snapshots logged as warnings, DB records pointing to missing snapshots marked as `gc_reset`.

---

## 7. CI integration

The controller is CI-system-agnostic. The runner-side integration (pre-job hook, post-job hook) must be implemented per CI system. This section shows the **contract** (what any CI integration must do) and provides **GitLab CI** as the reference implementation.

### 7.1 CI integration contract

Every CI integration must:

1. **Pre-job hook**: create a checkpoint and export `checkpoint_name` for the post-job hook.
2. **Run the test script**: project-defined, no controller involvement.
3. **Post-job hook** (runs even on failure):
   a. Upload artifacts to the CI system first.
   b. Finalize the checkpoint with `status` (success/failure/canceled).
4. **Runner config**: `concurrent = 1` (one job at a time per runner). Tags/labels route jobs to the correct runners.

**Two integration modes:**

| | Agent (preferred) | Direct HTTP (legacy) |
|-|-------------------|---------------------|
| **Pre-job** | `e2epool create --job-id $CI_JOB_ID` | `curl -X POST .../checkpoint/create` with Bearer token |
| **Post-job** | `e2epool finalize --checkpoint $NAME --status $STATUS` | `curl -X POST .../checkpoint/finalize` with Bearer token |
| **Auth** | Token in agent config on runner host | Bearer token in CI variable |
| **Network** | Egress only (agent opens outbound WebSocket) | Runner needs ingress to controller HTTP |
| **Dependencies** | `e2epool` CLI installed on runner | `curl`, `jq` |

The agent mode is preferred because it eliminates ingress firewall rules and keeps credentials out of CI variables.

**CI environment variable mapping:**

| Concept | GitLab CI | GitHub Actions | Jenkins |
|---------|-----------|----------------|---------|
| Job ID | `$CI_JOB_ID` | `$GITHUB_RUN_ID` | `$BUILD_NUMBER` |
| Job status | `$CI_JOB_STATUS` | job conclusion | `$BUILD_RESULT` |
| Runner name | `$CI_RUNNER_DESCRIPTION` | `$RUNNER_NAME` | `$NODE_NAME` |
| Pre-job hook | `before_script` | composite action `pre` | pipeline `pre` stage |
| Post-job hook | `after_script` | composite action `post` | pipeline `post` stage / `always` block |

### 7.2 CI adapter interface

The controller uses a **CI adapter** to interact with the CI system. Each adapter implements three methods:

```
class CIAdapter:
    def get_job_status(job_id) -> str:
        """Query the CI API for job status. Returns 'running', 'success', 'failed', or 'canceled'."""

    def pause_runner(runner_id):
        """Prevent the CI system from dispatching new jobs to this runner."""

    def unpause_runner(runner_id):
        """Allow the CI system to dispatch jobs to this runner again."""
```

**GitLab adapter** (primary): queries `GET /api/v4/jobs/:id` (`read_api` scope), pauses/unpauses runners via `PUT /api/v4/runners/:id` (`admin` scope or runner owner token with `manage_runner` permission). Note: pausing a runner requires elevated permissions beyond `read_api`.
**GitHub Actions adapter** (future): queries `GET /repos/:owner/:repo/actions/runs/:id` (`actions:read` scope), disables/enables self-hosted runners via `PUT /repos/:owner/:repo/actions/runners/:id` (`admin:org` or repo admin scope).
**Jenkins adapter** (future): queries `GET /job/:name/:id/api/json`, marks node offline/online via `POST /computer/:name/toggleOffline`.

### 7.3 Reference: GitLab CI

#### Runner configuration

Each runner **must** have `concurrent = 1`. Tags determine which runners pick up the job. Checkpoint creation is handled automatically by the GitLab runner's `pre_build_script` hook.

```toml
# /etc/gitlab-runner/config.toml (on each runner — VM or bare-metal)
concurrent = 1

[[runners]]
  name = "e2e-runner-01"
  url = "https://gitlab.example.com"
  token = "RUNNER_TOKEN"
  executor = "shell"
  pre_build_script = """
export CHECKPOINT=$(e2epool create --job-id "$CI_JOB_ID") || { echo "Failed to create checkpoint"; exit 1; }
echo "Checkpoint created - $CHECKPOINT"
"""
  [runners.custom_build_dir]
  [runners.cache]
```

**Important:** The `pre_build_script` hook handles checkpoint creation automatically for all jobs on this runner. Projects do **not** need to define `before_script` for checkpoint creation — it happens transparently.

Finalization is handled automatically by the controller's **job status poller** and/or **GitLab webhook** (`POST /webhook/gitlab`). Projects do **not** need to define `after_script` for finalization. The controller detects job completion and triggers finalize automatically.

**Note:** `post_build_script` is **not used** because `$CI_JOB_STATUS` is always `running` at that point (the job hasn't finished yet). Finalization relies on the job status poller and webhook instead.

Minimum GitLab version: **13.0+** (required for `pre_build_script`).

#### Job templates

Projects only need to define their test `script` block. Checkpoint creation is handled automatically by the runner's `pre_build_script` hook. Finalization is handled automatically by the controller's job status poller and/or GitLab webhook.

**No per-project CI configuration needed:**

```yaml
stages:
  - test

e2e_tests:
  stage: test
  tags:
    - e2e
  script:
    # Just run your tests — checkpoint creation and finalization are automatic
    - npm run test:e2e
  artifacts:
    when: always
    paths:
      - test-results/
    reports:
      junit: test-results/junit.xml
```

The `pre_build_script` in the runner's `config.toml` creates the checkpoint before the job starts. The controller detects job completion via polling or webhook and finalizes automatically.

**Legacy: manual integration** (if not using `pre_build_script`): use `before_script`/`after_script` with `curl` calls to the checkpoint API. See the controller API contract (section 6.1) for endpoint details. This approach requires storing `RUNNER_TOKEN` as a CI variable and having network ingress from runners to the controller.

### 7.4 Reference: GitHub Actions (sketch)

For repositories on GitHub, the same controller API is used. Note: composite actions don't have native `pre`/`post` lifecycle hooks (only JavaScript actions do). Instead, use explicit workflow steps with `if: always()` for the post-job finalize:

```yaml
# .github/workflows/e2e.yml
name: E2E Tests
on: [push]
jobs:
  e2e:
    runs-on: [self-hosted, e2e]
    steps:
      # ── Pre-job: create checkpoint ─────────────────────
      - name: Create checkpoint
        id: checkpoint
        run: |
          RUNNER_ID=$(cat /etc/runner_id)
          RESPONSE=$(curl -sf -X POST "${{ secrets.CONTROLLER_URL }}/checkpoint/create" \
            -H "Authorization: Bearer ${{ secrets.RUNNER_TOKEN }}" \
            -H "Content-Type: application/json" \
            -d "{\"runner_id\": \"$RUNNER_ID\", \"job_id\": \"${{ github.run_id }}\", \"caller\": \"${{ runner.name }}\"}")
          echo "name=$(echo $RESPONSE | jq -r .name)" >> $GITHUB_OUTPUT

      # ── Test script (project-specific) ─────────────────
      - name: Run E2E tests
        run: |
          docker compose -f docker-compose.e2e.yml up -d
          docker compose -f docker-compose.e2e.yml exec backend pytest tests/e2e

      # ── Post-job: upload artifacts then finalize ───────
      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: test-results/

      - name: Finalize checkpoint
        if: always()
        run: |
          RUNNER_ID=$(cat /etc/runner_id)
          STATUS="${{ job.status }}"  # success, failure, canceled
          curl -sf -X POST "${{ secrets.CONTROLLER_URL }}/checkpoint/finalize" \
            -H "Authorization: Bearer ${{ secrets.RUNNER_TOKEN }}" \
            -H "Content-Type: application/json" \
            -d "{\"checkpoint_name\": \"${{ steps.checkpoint.outputs.name }}\", \"status\": \"$STATUS\", \"source\": \"hook\"}" \
            --max-time 10 --retry 2 || true
```

### 7.5 Job status poller (fallback finalize)

The controller runs a background poller that detects missed finalizes via the **CI adapter** — no inbound network exposure needed (no pfSense NAT rules, no public IPs). This works identically for both runner backends and any CI system.

**How it works:**

1. When `POST /checkpoint/create` succeeds, the controller records the `job_id` alongside the checkpoint.
2. A background Celery beat task runs every **20 seconds** (`E2EPOOL_POLLER_INTERVAL_SECONDS`) and queries the CI system's API (via the CI adapter) for each active checkpoint older than 2 minutes (`E2EPOOL_POLLER_MIN_AGE_SECONDS`):
   * GitLab: `GET /api/v4/jobs/<job_id>` with `read_api` token
3. If the job status is terminal (`success`, `failed`, `canceled`), the controller queues a finalize for that checkpoint.
4. If finalize was already triggered by the post-job hook or webhook, the poller's finalize is a no-op (idempotent).

**Configuration:**

* CI API credentials: `E2EPOOL_GITLAB_URL` and `E2EPOOL_GITLAB_TOKEN` (scope: `read_api` for GitLab).
* `E2EPOOL_POLLER_INTERVAL_SECONDS`: polling frequency (default: 20s).
* `E2EPOOL_POLLER_MIN_AGE_SECONDS`: skip checkpoints newer than this (default: 120s) to avoid race conditions with newly started jobs.
* `E2EPOOL_POLLER_ENABLED`: set to `false` to disable poller when webhooks are configured.

This ensures the controller learns about job completion even if the post-job hook never fires (runner crash, power loss, job timeout, user cancellation). The worst-case detection delay is one poll interval + min age (~2.5 min).

---

## 8. VM specification

The pool is **shared across all projects**. Runners are generic: each includes a CI runner agent (e.g., GitLab Runner, GitHub Actions runner) and the tooling needed for its platform. Each project defines its own test script in the CI job — the runner and controller don't enforce any particular test framework. Runners are sized for the heaviest workload on their platform.

### 8.1 Proxmox runner VM spec

| Resource | Spec | Rationale |
|----------|------|-----------|
| **CPU** | 4 vCPUs | Sized for heaviest project (Voltavo: backend + Celery worker + PostgreSQL + Redis concurrently). Lighter projects (pvwebapp) will underutilize but this keeps the pool simple. |
| **RAM** | 8 GB | Voltavo peak: ~5-6 GB (backend with LibreOffice ~1.5 GB, PostgreSQL ~1 GB, Celery ~512 MB, Redis ~256 MB, Docker overhead). 8 GB provides headroom for any project. |
| **Disk** | 64 GB | OS (~5 GB), Docker images (~4-6 GB per project), Docker volumes, test artifacts, snapshot COW overhead. |
| **Network** | Static IP (internal, via pfSense DHCP reservation) | Must survive reboots and rollbacks. No public IP needed. |
| **OS** | Ubuntu 24.04 LTS (minimal server) | Generic base. Minimal install + Docker CE + CI runner agent. No project-specific software baked in. |

### 8.2 Bare-metal runner spec (Mac Mini)

Mac Minis in the office serve mobile E2E tests (iOS simulators, Android emulators). They are **not** Proxmox VMs — the controller manages them via SSH over the VPN tunnel.

| Resource | Spec | Rationale |
|----------|------|-----------|
| **Hardware** | Mac Mini (M1/M2/M4, 16 GB+ RAM) | Apple silicon for iOS simulator performance. 16 GB minimum for Xcode + simulator + Android emulator concurrently. |
| **OS** | macOS (latest stable) | Required for Xcode and iOS simulators. |
| **Software** | Xcode, Android SDK, CI runner agent (e.g., GitLab Runner), `jq`, `curl` | Tooling for both platforms. |
| **Network** | Static office LAN IP + WireGuard VPN client (or office router VPN) | Must be reachable from the controller instances. |
| **Runner ID** | Configured in `/etc/e2epool/agent.yml` (e.g., `mac-01`) | Identifies the machine to the controller. |
| **Concurrent** | `1` (same invariant as Proxmox runners) | One job at a time per Mac Mini. |

**Key difference from Proxmox runners:** no hypervisor snapshots. Reset is script-based (`reset_cmd` in inventory). The `reset_cmd` must be thorough enough to return the machine to a clean state (kill processes, erase simulators, clear derived data).

### 8.3 Controller instance spec

The controller is a **stateless service** that can run on any platform. Each instance needs:

| Resource | Spec | Rationale |
|----------|------|-----------|
| **CPU** | 1-2 vCPUs | IO-bound (HTTP API, DB queries, SSH, REST calls). Not compute-heavy. |
| **RAM** | 512 MB - 1 GB | Lightweight Python/Go service. No large in-memory state. |
| **Disk** | Minimal (< 1 GB) | Stateless; only needs the application binary/image. Logs can go to stdout/external. |
| **Network** | Must reach: runners (SSH), PVE API, CI API, shared PostgreSQL | If outside the internal network, join via WireGuard VPN. |

**Deployment options (non-exhaustive):**

| Platform | How |
|----------|-----|
| Proxmox VM | Small VM (1 vCPU, 1 GB RAM) on any node. Cheapest if using existing Proxmox capacity. |
| Hetzner Cloud | CX22 or similar (~2 EUR/mo). Easy to spin up, no Proxmox dependency. Connect to internal network via VPN. |
| Docker container | On any Docker host. Pass `DATABASE_URL` as env var. |
| Kubernetes | Deployment with N replicas + Service. Inventory as ConfigMap. |
| Bare-metal | systemd service on any Linux/macOS machine. |

For **multiple instances**, place them behind a load balancer (HAProxy, Nginx, cloud LB), floating IP, or DNS round-robin. Runners send checkpoint API calls to the stable endpoint.

### 8.4 Shared database spec

| Resource | Spec | Rationale |
|----------|------|-----------|
| **Engine** | PostgreSQL 14+ | Advisory locks, JSONB, mature ecosystem. |
| **CPU** | 1-2 vCPUs | Low query volume (proportional to number of runners, not test traffic). |
| **RAM** | 1-2 GB | Small working set (checkpoint records, locks). |
| **Disk** | 5-10 GB | Checkpoint records + operation logs. Grows slowly. |
| **Deployment** | Co-located VM, managed cloud DB (Hetzner, AWS RDS), or shared existing PostgreSQL | Must be reachable by all controller instances. |

For v1, a PostgreSQL instance on the same network as the controllers (Proxmox VM, Hetzner Cloud) is sufficient. A managed database service simplifies backups and HA.

### 8.5 Infrastructure VM specs (Node 1)

These VMs run on **Node 1** and serve the runner pool. The controller is **not** included here — it runs independently (see section 8.3).

| VM | CPU | RAM | Disk | Role |
|----|-----|-----|------|------|
| pfSense (100) | 2 | 2 GB | 16 GB | Router, DHCP, firewall for internal network |
| Golden template (400) | - | - | 64 GB | Stopped. Cloned to create new runners on any node. |

---

## 9. Provisioning & runner identity

### 9.1 Runner identity

* The runner ID is configured in the agent config file (`/etc/e2epool/agent.yml`) at provisioning time (e.g., `e2e-runner-01` for Proxmox, `mac-01` for bare-metal).
* Assign static IPs (Proxmox: pfSense DHCP reservation; bare-metal: office LAN static IP). IPs must be stable.
* Proxmox VMs: configure NTP with `chronyc makestep` in boot sequence to correct clock skew after rollback.

### 9.2 Proxmox runner registration

**Automated provisioning:**

Runner provisioning is fully automated via scripts in the `scripts/` directory.

**Create a runner:**

```bash
scripts/create-e2e-runner.sh \
  --vmid 401 --ip 10.0.0.41 \
  --runner-id e2e-runner-01 \
  --admin-token $ADMIN_TOKEN \
  --gitlab-url https://gitlab.example.com \
  --gitlab-token glrt-xxxxxxxxxxxxxxxxxxxx \
  --proxmox-token-name e2epool \
  --proxmox-token-value xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

This script performs all steps automatically:
1. Renders cloud-init template (`scripts/e2e-runner.yaml`) with hostname and SSH key
2. Clones VM from template and configures networking
3. Starts VM and waits for SSH + cloud-init completion
4. Registers GitLab runner and configures `pre_build_script` checkpoint hook
5. Registers runner via e2epool admin API (auto-generates token)
6. Installs e2epool agent and starts systemd service

**Destroy a runner:**

```bash
scripts/destroy-e2e-runner.sh \
  --vmid 401 --runner-id e2e-runner-01 \
  --admin-token $ADMIN_TOKEN --force
```

The cloud-init template (`scripts/e2e-runner.yaml`) pre-installs all required packages (Docker, GitLab runner, Python, etc.).

**Manual steps** (if not using automated scripts):

* Use cloud-init to register the runner and pass a one-time registration token from Vault.
* Set `concurrent = 1` in runner config (`e2e` tag).
* Configure `pre_build_script` in `/etc/gitlab-runner/config.toml` for checkpoint creation.
* Register runner via admin API with `backend: proxmox`.

### 9.3 Bare-metal runner registration (Mac Mini)

1. **Network**: ensure the Mac Mini is reachable from all controller instances. Options:
   * **WireGuard on office router**: site-to-site VPN from office router to pfSense on Node 1. All office machines are reachable via VPN subnet (e.g., `10.10.0.0/24`).
   * **WireGuard on each Mac**: each Mac Mini runs a WireGuard client connecting to pfSense. More granular control, works if the office router doesn't support VPN.
2. **Install tooling**: Xcode (with command line tools), Android SDK (if needed), CI runner agent (e.g., `gitlab-runner install`), `jq`, `curl`.
3. **Register CI runner**: register with the CI system using `concurrent=1`, `e2e-mobile` tag.
4. **Configure agent**: create `/etc/e2epool/agent.yml` with `runner_id`, `token`, and `controller_url`.
5. **Create reset/cleanup scripts**: `~/scripts/reset-mobile-env.sh` (see section 6.2 for example).
6. **Install e2epool agent**: `pip install e2epool`, create `/etc/e2epool/agent.yml` with the runner's `runner_id` and token, enable as a system service (see README for systemd/launchd setup).
7. **Register via admin API**: `POST /api/runners` with `backend: bare_metal`, reset/cleanup commands.
8. **Verify**: run a smoke test mobile E2E job.

### 9.4 Adding a new Proxmox node

To scale the Proxmox pool to a new physical server:

1. **Provision server**: order a Hetzner dedicated server (or equivalent), install Proxmox VE.
2. **Network**: connect to the internal network. Options:
   * **Same datacenter (recommended)**: use Hetzner vSwitch or VLAN to connect nodes at L2. pfSense on Node 1 provides DHCP/routing for the shared subnet.
   * **Different datacenter**: WireGuard or IPsec VPN tunnel between nodes. pfSense manages the VPN endpoint.
3. **Clone golden template**: copy `e2e-basevm` (VMID 210) to the new node via `qm clone` with `--target <new-node>` (if Proxmox cluster) or export/import the template.
4. **Create runner VMs**: clone from the local golden template copy. Assign VMIDs in the new node's range (e.g., 311-313). Set static IPs.
5. **Register runners**: register each new runner with the CI system (`concurrent=1`, `e2e` tag).
6. **Register runners**: register each via admin API (`POST /api/runners`) with `backend: proxmox`, node name, and PVE API credentials.
7. **Verify**: run a smoke test E2E job on each new runner.

No changes to the controller code are needed — it dispatches to the correct backend based on the inventory.

### 9.5 Controller deployment

The controller is a stateless service. Deploy one or more instances on any platform with network access to runners, PVE API, CI API, and the shared database.

1. **Provision shared database**: set up a PostgreSQL instance (Proxmox VM, managed cloud DB, or existing PostgreSQL). Run the schema migration.
2. **Deploy controller instance(s)**:
   * **Docker**: `docker compose up -d` (uses Nginx reverse proxy on port 8080)
   * **Proxmox VM**: small VM (1 vCPU, 1 GB RAM), install the controller as a systemd service.
   * **Hetzner Cloud**: CX22 instance, Docker or systemd. Connect to the internal network via WireGuard VPN.
   * **Kubernetes**: Deployment with `replicas: N`, Secret for credentials.
3. **Network connectivity**: if the controller is outside the internal network, set up WireGuard VPN so it can reach runner IPs (SSH) and PVE API endpoints.
4. **Stable endpoint**: configure a load balancer, floating IP, or DNS record pointing to the controller instance(s). All runners use this endpoint for checkpoint API calls.
5. **Environment variables** (all prefixed with `E2EPOOL_`):
   * `E2EPOOL_DATABASE_URL` (required) — PostgreSQL connection string
   * `E2EPOOL_REDIS_URL` — Redis for Celery broker (default: `redis://localhost:6379/0`)
   * `E2EPOOL_ADMIN_TOKEN` — bearer token for admin API
   * `E2EPOOL_GITLAB_URL`, `E2EPOOL_GITLAB_TOKEN` — GitLab CI adapter settings
   * `E2EPOOL_GITLAB_WEBHOOK_SECRET` — GitLab webhook verification
   * `E2EPOOL_GITHUB_WEBHOOK_SECRET` — GitHub webhook verification
   * `E2EPOOL_CI_PROVIDER` — CI adapter to use (default: `gitlab`)
   * `E2EPOOL_POLLER_ENABLED` — disable poller when webhooks are configured (default: `true`)
6. **Verify**: run `curl https://<controller-endpoint>/healthz` from a runner VM and from the operator machine.

To add another instance, repeat step 2 with the same config. No coordination needed beyond shared DB access.

---

## 10. Golden image lifecycle

The golden image is a **project-agnostic** Proxmox VM template from which all Proxmox runner VMs are cloned. It contains only generic tooling — no project-specific code or images. (Bare-metal runners like Mac Minis are provisioned manually; see section 9.3.)

### 10.1 Image contents

* OS with security patches, Docker CE, CI runner agent (e.g., GitLab Runner), `jq`, `curl`, Node.js (for Playwright/frontend tests).
* Docker daemon configured to trust the local registry CA (`registry.internal:5000`).
* Pre-pulled **base images shared across projects** (pulled from local registry):
  * `postgres:14`, `postgres:12`, `redis:7-alpine`, `nginx:alpine`
* No project-specific application images baked in. Projects pull their own images from the local registry at job start. Since the registry is on the LAN, pulls are fast (~seconds).
* NTP configured with `makestep` for clock correction after rollback.
* Agent config placeholder (`/etc/e2epool/agent.yml` is written per-VM at provisioning time with runner_id, token, and controller URL).

### 10.2 Update procedure

1. Clone the current golden template to a staging VM.
2. Apply updates (OS patches, base image pulls, runner upgrades).
3. Run a smoke test E2E job for each project on the staging VM.
4. If passed, convert the staging VM to the new golden template.
5. Rolling update: drain one pool VM at a time (pause runner via CI adapter), re-clone from new template, re-register runner, unpause. Repeat for each VM.
6. Keep the previous golden template for one cycle as a rollback option.

### 10.3 Update frequency

* Security patches: as needed (triggered by CVE alerts).
* Base image cache refresh: weekly or when upstream images change.
* Runner agent upgrades: aligned with CI system version upgrades.

---

## 11. Garbage collection and safety

* Background GC (Celery beat task every `E2EPOOL_GC_INTERVAL_SECONDS`, default 60s) sweeps checkpoints with state `created` older than `checkpoint_ttl` (default: **30 minutes**, configurable via `E2EPOOL_CHECKPOINT_TTL_SECONDS`). GC transitions stale checkpoints directly to `gc_reset` state (bypassing `finalize_queued`). Checkpoints in `finalize_queued` are handled by startup reconciliation, not GC. A short TTL is critical: a dirty runner sitting idle for hours wastes capacity.
* For each stale checkpoint, controller should:

  1. Acquire lock.
  2. Pause runner via CI adapter (prevent new jobs).
  3. Reset the runner (Proxmox: rollback snapshot; bare-metal: run `reset_cmd`).
  4. Delete checkpoint (Proxmox: delete snapshot; bare-metal: delete record).
  5. Wait for runner readiness.
  6. Unpause runner via CI adapter.
  7. Mark record as GC'd and emit metric/alert.
* Reset/delete operations should be retried with exponential backoff on failure and cause alerts if persistent.
* **Proactive cleanup for successful jobs**: even without reset, successful jobs can leave behind state (Docker containers, temp files, etc.). If `cleanup_cmd` is configured in the inventory (see section 6.2), the controller runs it after successful finalize. For Proxmox VMs, consider a periodic proactive rollback to golden state every N successful jobs (e.g., every 50) or every 24 hours to prevent drift.

---

## 12. Security and access control

* **Per-runner authentication:** each runner receives a unique Bearer token (or mTLS client cert) scoped to its own runner ID. The controller rejects operations on IDs that don't match the token. Tokens are stored in Vault and rotated regularly. For WebSocket connections, the same token is passed as a query parameter (`?runner_id=X&token=Y`) and verified on connect — invalid credentials result in an immediate close with code `4401`.
* **Rate limiting:** creation is rejected while an active checkpoint exists for the runner (the single-checkpoint invariant). Additionally, apply a cooldown of 5 seconds after finalize completes before accepting a new create — this prevents rapid cycling from misconfigured retry loops, while allowing normal sequential jobs.
* **Input validation:** all request fields are strictly validated: `runner_id` must exist in inventory, `job_id` must be a non-empty alphanumeric string, checkpoint names must match the expected pattern. Any non-conforming input is rejected.
* **Audit logging:** all controller operations (create, finalize, reset, delete, GC) are logged as structured JSON with timestamp, caller identity, runner ID, job ID, backend type, and result. Logs written to stdout (aggregated externally in multi-instance setups) or to the shared database.
* **Proxmox credentials:** dedicated PVE API user (`e2epool@pve`) with privileges limited to `VM.Snapshot`, `VM.Snapshot.Rollback`, `VM.PowerMgmt` on pool VM IDs only. One API token per node if using separate Proxmox instances, or a single cluster-wide token if using a Proxmox cluster.
* **Bare-metal SSH credentials:** all controller instances use the same dedicated SSH key pair for executing `reset_cmd`/`cleanup_cmd` on bare-metal runners. The key is scoped to a restricted user on the Mac Mini (no root). Private key distributed to all instances (via Vault, secrets manager, or mounted volume).
* **Database credentials:** `DATABASE_URL` contains the PostgreSQL connection string. Stored as an environment variable or loaded from Vault. The database should require TLS for connections from outside the local network.
* **VPN security (office link):** the WireGuard tunnel between the office and pfSense uses pre-shared keys + public key cryptography. Only the VPN subnet is routable between sites — no broad network access. pfSense firewall rules restrict office traffic to the controller API port and SSH (for reset commands).
* **Controller network position:** if the controller runs outside the internal network (e.g., Hetzner Cloud), it must join via WireGuard VPN. The VPN configuration should allow the controller to reach runner IPs and PVE API endpoints but not expose unnecessary services.
* **Network ACLs:** controller API is accessible only from runner subnets (datacenter + VPN) and operator machines. The shared database is accessible only from controller instances. With the WebSocket agent, runners only need **egress** to the controller — no ingress firewall rules are required. The agent config file (`/etc/e2epool/agent.yml`) should be readable only by the service user (mode `0600`).
* **WebSocket agent security:** the agent stores the runner token in its local config file, not in CI variables. The Unix domain socket (`/var/run/e2epool-agent.sock`) is created with mode `0660` so only authorized local processes can send commands. The WebSocket connection uses the same token-per-runner scheme as HTTP Bearer auth — each agent authenticates as a specific runner_id on connect.
* **CI API token:** the poller uses a read-only API token to query job status (e.g., `read_api` scope for GitLab, `actions:read` for GitHub). Token stored in Vault or environment variable, rotated regularly. No inbound network exposure required.
* **Secrets in multi-instance:** all secrets (PVE token, CI API token, SSH key, DB credentials, runner tokens) must be available to every controller instance. Use a secrets manager (Vault, cloud KMS) or environment variables — never bake secrets into the container image.
* **Controller API TLS:** the controller API **must** use TLS. For v1 with a single endpoint behind the internal network, a self-signed CA is acceptable (runners trust the CA cert, deployed via golden image). For multi-instance behind a load balancer, terminate TLS at the load balancer. mTLS (runner client certs) is recommended for production but per-runner Bearer tokens are sufficient for v1.
* **CI API token scope:** the poller needs `read_api` scope for GitLab. The `pause_runner`/`unpause_runner` operations need a separate token with `manage_runner` or admin-level scope (see section 7.2). Keep these as separate tokens with minimal privilege.
* **Database backups:** the shared PostgreSQL database is the single source of truth. Back up daily (pg_dump or WAL archiving). Retention: 7 days minimum. For managed databases (Hetzner, AWS RDS), enable automated backups. For self-managed, set up a cron job on the DB host. Test restore procedure during the pilot phase.

---

## 13. Storage capacity planning (Proxmox backend)

Proxmox snapshots consume storage proportional to writes during the job. Bare-metal runners don't have this concern. This section applies to Proxmox nodes only.

### 13.1 Sizing

* **Snapshot overhead per job:** depends on storage backend and test workload. Docker image pulls, container filesystem writes, and test data generation all contribute. Estimate 2-10 GB per job for a typical E2E suite (measure during pilot).
* **Concurrent capacity:** with 3 VMs and `concurrent=1`, at most 3 active snapshots exist at any time. Worst case: 3 * 10 GB = 30 GB snapshot overhead.
* **Storage backend considerations:**
  * **LVM-thin:** fast snapshots, but thin pool exhaustion freezes all VMs on that storage. Set overprovisioning alerts.
  * **ZFS:** fast snapshots with low overhead, good default choice.
  * **Ceph:** distributed but slower snapshot operations, higher latency.

### 13.2 Monitoring

* Alert at **70% storage pool usage** (Proxmox storage pool, not OS disk).
* Track `snapshot_disk_usage_bytes` per VM.
* Alert if any snapshot exceeds expected size threshold.

---

## 14. Observability

### 14.1 v1 (pilot) - no dedicated monitoring stack

For the pilot with 3 VMs, a dedicated Prometheus/Loki stack is unnecessary overhead. Use:

* **Controller structured logs** (JSON to stdout/file): all lifecycle operations are logged with timestamp, runner ID, job ID, backend, operation, duration, and result. Sufficient for debugging and post-incident review.
* **Proxmox built-in monitoring**: CPU, RAM, disk, and network graphs per VM in the Proxmox web UI. No setup needed.
* **`qm listsnapshot <vmid>`**: manual snapshot inspection for Proxmox runners.
* **Controller `/healthz` endpoint**: basic liveness check, can be polled by a simple cron + curl script that alerts via email or Slack on failure.

### 14.2 Key data points to log

The controller should log these for each operation (structured JSON):

* `checkpoint_create_duration_seconds`
* `reset_duration_seconds` (rollback for Proxmox, script for bare-metal)
* `checkpoint_delete_duration_seconds`
* `readiness_duration_seconds`
* `finalize_source` (`post-job hook` vs `poller` — tracks how often the poller is the fallback)
* `backend_type` (`proxmox` vs `bare_metal`)
* `pending_checkpoints_count` (logged periodically by GC)

### 14.3 Alerting (lightweight)

A simple health check (cron script, external monitor, or built into the controller) that sends notifications (email/Slack webhook):

* Controller process not running
* Stale checkpoints (age > TTL) exist
* Storage pool usage > 70% (query Proxmox API) — Proxmox runners only
* Runner readiness timeout after reset (detected from controller logs)
* VPN tunnel down (ping office subnet gateway) — bare-metal runners unreachable

### 14.4 Future: Prometheus + Loki

If the pilot reveals a need for historical dashboards or more sophisticated alerting, deploy Prometheus + Loki + Grafana (dedicated VM or alongside the controller). The controller exposes a `/metrics` endpoint for Prometheus to scrape. This is an incremental upgrade, not a v1 requirement.

---

## 15. Failure modes and mitigations

| Failure | Backend | Mitigation |
|---------|---------|-----------|
| **Checkpoint creation slow/fails** | proxmox | Job fails fast at pre-job hook. Alert operator. Investigate storage IO. |
| **Runner never calls finalize** (crash, network partition) | all | Job status poller detects completion via CI API within ~30s. If both fail, GC catches within 30 min TTL. |
| **Single controller instance crashes** | all | Other instances continue serving. On restart, the crashed instance picks up `finalize_queued` records from the shared DB and re-enqueues. All operations are idempotent. |
| **All controller instances down** | all | Runners can't create/finalize checkpoints. Jobs fail at pre-job hook. On restart, any instance reconciles the shared DB against Proxmox snapshot list (for VMs) and checkpoint records (for bare-metal). |
| **Shared database down** | all | All controller instances fail. No checkpoint operations possible. Jobs fail at pre-job hook. Restore DB from backup; checkpoint state is recoverable. |
| **Runner readiness timeout after reset** | all | Runner stays paused via CI adapter. Alert for manual investigation. |
| **Concurrent jobs on same runner** (misconfiguration) | all | Controller returns `409` on second `create`. Job fails fast. Signals `concurrent` misconfiguration. |
| **Storage pool exhaustion** | proxmox | Alert at 70%. GC aggressively cleans stale snapshots. Proactive rollback for long-running successful VMs. |
| **Network partition (runner <-> controller)** | all | Pre-job hook fails, job fails. Post-job hook finalize retries with backoff. Poller provides independent path (outbound to CI API). |
| **VPN tunnel down** | bare_metal | Controller can't SSH to office runners. Reset fails, runner stays paused. Poller still works (outbound to CI API). VPN auto-reconnect should recover. Alert on tunnel down. |
| **`reset_cmd` fails on bare-metal** | bare_metal | Runner in unknown state. Controller pauses runner, alerts operator. Manual SSH investigation required. |
| **Clock skew after rollback** | proxmox | NTP with `chronyc makestep` in boot sequence corrects clock immediately. |
| **Docker image cache lost on rollback** | proxmox | Pre-pull images into golden template. Application images served by local registry VM (see [Local Container Registry RFC](./local-container-registry.md)). |
| **Mac Mini power loss / reboot** | bare_metal | Runner comes back up after reboot. CI runner service auto-starts. Controller detects readiness via SSH. Any stale checkpoint is GC'd. |

---

## 16. Scaling strategy

### 16.1 Current capacity

* **Proxmox runners:** 3 VMs (`concurrent=1` each) → 3 parallel web/backend E2E jobs. Jobs from Voltavo and pvwebapp share the `e2e` tag queue.
* **Bare-metal runners:** 2 Mac Minis (`concurrent=1` each) → 2 parallel mobile E2E jobs on the `e2e-mobile` tag queue.
* Excess jobs wait in the CI system's queue until a runner becomes available.

### 16.2 Scaling signals

* Monitor CI job queue wait time for `e2e` and `e2e-mobile` tagged jobs.
* If median wait time exceeds acceptable threshold (e.g., 10 minutes), add runners.
* If a single project dominates the queue and starves others, consider per-project tags — but only if contention becomes a real problem.

### 16.3 Adding Proxmox VMs (same node)

1. Clone from golden template on the same node.
2. Assign static IP, configure agent (`/etc/e2epool/agent.yml`).
3. Register runner with the CI system (`concurrent=1`, `e2e` tag).
4. Register runner via admin API (`POST /api/runners`) with `backend: proxmox`.
5. Verify with a smoke test job.

### 16.4 Adding a Proxmox node (horizontal scaling)

When a single node is full, add a new physical server. See section 9.4 for the full procedure. Summary:

1. Provision new Hetzner server, install Proxmox, connect to internal network.
2. Clone golden template to the new node.
3. Create runner VMs, register with the CI system and controller.

The controller manages VMs across all nodes transparently. No code changes needed.

### 16.5 Adding bare-metal runners (Mac Mini)

1. Set up Mac Mini in the office (see section 9.3).
2. Establish VPN connectivity to controller instances.
3. Install tooling, register CI runner with `e2e-mobile` tag.
4. Register via admin API (`POST /api/runners`) with `backend: bare_metal`.
5. Verify with a smoke test mobile job.

### 16.6 Adding controller instances

1. Deploy another controller instance on any platform (see section 9.5).
2. Point it at the same `DATABASE_URL`.
3. Add the instance to the load balancer / DNS record.
4. Verify with `curl /healthz`.

No coordination needed. The new instance immediately starts serving API requests, running the poller, and participating in GC.

### 16.7 Removing runners

1. Pause runner via CI adapter (no new jobs).
2. Wait for any active job to complete.
3. Finalize any remaining checkpoint.
4. Deactivate runner via admin API (`DELETE /api/runners/{runner_id}`).
5. Proxmox: destroy VM. Bare-metal: uninstall runner, remove VPN config.

### 16.8 Decommissioning a Proxmox node

1. Remove all VMs on the node (follow 16.7 for each).
2. Remove node from Proxmox cluster (if clustered) or just power off.
3. Cancel the Hetzner server.

Auto-scaling is out of scope for v1 but the manual procedures above can be scripted.

## 17. Operational runbook (summary)

| Scenario | Action |
|----------|--------|
| **Failed checkpoint create** | Job fails at pre-job hook. Proxmox: investigate storage IO and PVE API health. Bare-metal: check controller DB health. |
| **Failed finalize after success** | Controller retries with backoff. Proxmox: manual `qm delsnapshot` if persistent. Bare-metal: manual `cleanup_cmd` run. |
| **Stale checkpoints** | GC resets/deletes after 30 min TTL. If recurring, investigate flaky runners or network issues. |
| **Runner readiness timeout** | Runner stays paused. SSH into runner, check services. Manually unpause after fix. |
| **Storage pool > 70%** (Proxmox) | Check for stale snapshots. Run manual GC. Consider adding storage or reducing pool size. |
| **Single controller instance down** | Other instances continue serving. Restart the failed instance; it reconnects to the shared DB and resumes. |
| **All controller instances down** | Jobs fail at pre-job hook (no checkpoint = no test run). Start any instance; it reconciles on startup from the shared DB. |
| **Shared database down** | All controller instances fail. Restore DB; checkpoint state is recoverable. |
| **VPN tunnel down** | Mobile jobs fail. Check pfSense WireGuard status and office router. Proxmox runners unaffected. |
| **`reset_cmd` fails** (bare-metal) | Runner paused. SSH into Mac Mini, manually run reset script or fix the environment. Unpause runner. |

