#!/usr/bin/env bash
# destroy-e2e-runner.sh — Deactivate an e2epool runner and destroy its Proxmox VM.
#
# Run on the Proxmox host. Requires: qm, curl, jq.

set -euo pipefail

CONTROLLER_URL="https://e2epool.planville.site:8080"

usage() {
    cat <<'EOF'
Usage: destroy-e2e-runner.sh --vmid <VMID> --runner-id <ID> --admin-token <TOKEN>

Deactivates the runner via the admin API and destroys the Proxmox VM.

Required:
  --vmid          VM ID to destroy
  --runner-id     Runner identifier to deactivate
  --admin-token   Admin API bearer token

Optional:
  --controller-url  Controller base URL (default: https://e2epool.planville.site:8080)
  --force           Skip confirmation prompt
EOF
    exit 1
}

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }
die()  { err "$@"; exit 1; }

VMID=""
RUNNER_ID=""
ADMIN_TOKEN=""
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vmid)            VMID="$2"; shift 2 ;;
        --runner-id)       RUNNER_ID="$2"; shift 2 ;;
        --admin-token)     ADMIN_TOKEN="$2"; shift 2 ;;
        --controller-url)  CONTROLLER_URL="$2"; shift 2 ;;
        --force)           FORCE=true; shift ;;
        -h|--help)         usage ;;
        *)                 die "Unknown option: $1" ;;
    esac
done

[[ -n "$VMID" ]]        || die "Missing --vmid"
[[ -n "$RUNNER_ID" ]]   || die "Missing --runner-id"
[[ -n "$ADMIN_TOKEN" ]] || die "Missing --admin-token"

if ! $FORCE; then
    read -rp "Destroy VM $VMID and deactivate runner '$RUNNER_ID'? [y/N] " confirm
    [[ "$confirm" =~ ^[yY]$ ]] || { log "Aborted."; exit 0; }
fi

# Deactivate runner via API
log "Deactivating runner '$RUNNER_ID'..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X DELETE "${CONTROLLER_URL}/api/runners/${RUNNER_ID}" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}")

case "$HTTP_CODE" in
    200) log "Runner deactivated." ;;
    404) log "Runner not found or already deactivated (HTTP 404), continuing." ;;
    *)   err "API returned HTTP $HTTP_CODE, continuing with VM destruction." ;;
esac

# Stop and destroy VM
log "Stopping VM $VMID..."
qm stop "$VMID" 2>/dev/null || true
sleep 2

log "Destroying VM $VMID..."
qm destroy "$VMID" --purge

log "Done. VM $VMID destroyed, runner '$RUNNER_ID' deactivated."
