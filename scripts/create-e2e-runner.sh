#!/usr/bin/env bash
# create-e2e-runner.sh — Create a Proxmox VM, register it as an e2epool runner,
# and install + start the e2epool agent.
#
# Run on the Proxmox host. Requires: qm, curl, jq, ssh.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

TEMPLATE_VMID=400
STORAGE="local-zfs"
GATEWAY="10.0.0.1"
NAMESERVER="10.0.0.1"
PROXMOX_NODE="$(hostname)"
PROXMOX_HOST="88.99.145.221"
PROXMOX_USER="root@pam"

CONTROLLER_URL="https://e2epool.planville.site:8080"
WS_URL="wss://e2epool.planville.site:8080/ws/agent"
E2EPOOL_REPO="https://github.com/kadeksuryam/e2epool.git"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLOUD_INIT_TEMPLATE="${SCRIPT_DIR}/e2e-runner.yaml"
CLOUD_INIT_RENDERED="/var/lib/vz/snippets/.rendered.yaml"
CLOUD_INIT_SNIPPET="local:snippets/.rendered.yaml"
SSH_USER="ubuntu"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -q"

BOOT_TIMEOUT=120
TAGS="e2e,proxmox"
PROXMOX_TOKEN_NAME=""
PROXMOX_TOKEN_VALUE=""
GITLAB_URL=""
GITLAB_TOKEN=""
GITLAB_RUNNER_ID=""
DRY_RUN=false

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
    cat <<'EOF'
Usage: create-e2e-runner.sh --vmid <VMID> --ip <IP> --runner-id <ID> \
       --admin-token <TOKEN> --gitlab-url <URL> --gitlab-token <TOKEN> \
       --proxmox-token-name <NAME> --proxmox-token-value <VALUE> [OPTIONS]

Creates a Proxmox VM from template, registers it as a GitLab runner and
e2epool runner, and installs the e2epool agent on the VM.

Required:
  --vmid                 VM ID for the new VM (e.g., 401)
  --ip                   VM IP address on internal bridge (e.g., 10.0.0.41)
  --runner-id            Runner identifier (e.g., e2e-runner-01)
  --admin-token          e2epool admin API bearer token
  --gitlab-url           GitLab instance URL (e.g., https://gitlab.com)
  --gitlab-token         GitLab runner token (from GitLab UI, glrt-... prefix)
  --proxmox-token-name   Proxmox API token name
  --proxmox-token-value  Proxmox API token value

Optional:
  --gitlab-runner-id     GitLab runner ID (integer, from GitLab UI)
  --proxmox-node         Proxmox node name (default: auto-detected from hostname)
  --tags                 Comma-separated tags (default: e2e,proxmox)
  --ssh-user             SSH user on the VM (default: ubuntu)
  --controller-url       Controller base URL (default: https://e2epool.planville.site:8080)
  --dry-run              Show what would be done without executing

Example:
  create-e2e-runner.sh \
    --vmid 401 \
    --ip 10.0.0.41 \
    --runner-id e2e-runner-01 \
    --admin-token my-admin-token \
    --gitlab-url https://gitlab.com \
    --gitlab-token glrt-xxxxxxxxxxxxxxxxxxxx \
    --gitlab-runner-id 12345 \
    --proxmox-token-name e2epool \
    --proxmox-token-value xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
EOF
    exit 1
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }
die()  { err "$@"; exit 1; }

run() {
    if $DRY_RUN; then
        log "[dry-run] $*"
    else
        "$@"
    fi
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

VMID=""
IP=""
RUNNER_ID=""
ADMIN_TOKEN=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vmid)                 VMID="$2"; shift 2 ;;
        --ip)                   IP="$2"; shift 2 ;;
        --runner-id)            RUNNER_ID="$2"; shift 2 ;;
        --admin-token)          ADMIN_TOKEN="$2"; shift 2 ;;
        --proxmox-token-name)   PROXMOX_TOKEN_NAME="$2"; shift 2 ;;
        --proxmox-token-value)  PROXMOX_TOKEN_VALUE="$2"; shift 2 ;;
        --proxmox-node)         PROXMOX_NODE="$2"; shift 2 ;;
        --gitlab-url)           GITLAB_URL="$2"; shift 2 ;;
        --gitlab-token)         GITLAB_TOKEN="$2"; shift 2 ;;
        --gitlab-runner-id)     GITLAB_RUNNER_ID="$2"; shift 2 ;;
        --tags)                 TAGS="$2"; shift 2 ;;
        --ssh-user)             SSH_USER="$2"; shift 2 ;;
        --controller-url)       CONTROLLER_URL="$2"; shift 2 ;;
        --dry-run)              DRY_RUN=true; shift ;;
        -h|--help)              usage ;;
        *)                      die "Unknown option: $1" ;;
    esac
done

[[ -n "$VMID" ]]         || die "Missing --vmid"
[[ -n "$IP" ]]           || die "Missing --ip"
[[ -n "$RUNNER_ID" ]]    || die "Missing --runner-id"
[[ -n "$ADMIN_TOKEN" ]]  || die "Missing --admin-token"
[[ -n "$GITLAB_URL" ]]          || die "Missing --gitlab-url"
[[ -n "$GITLAB_TOKEN" ]]        || die "Missing --gitlab-token"
[[ -n "$PROXMOX_TOKEN_NAME" ]]  || die "Missing --proxmox-token-name"
[[ -n "$PROXMOX_TOKEN_VALUE" ]] || die "Missing --proxmox-token-value"

# Derive WS URL from controller URL
WS_URL="${CONTROLLER_URL/https:/wss:}/ws/agent"
WS_URL="${WS_URL/http:/ws:}"

# Convert comma-separated tags to JSON array
IFS=',' read -ra TAG_ARRAY <<< "$TAGS"
TAGS_JSON=$(printf '%s\n' "${TAG_ARRAY[@]}" | jq -R . | jq -s .)

# ---------------------------------------------------------------------------
# Step 0: Render cloud-init template
# ---------------------------------------------------------------------------

log "=== Step 0: Rendering cloud-init template ==="

SSH_PUBKEY=$(cat /root/.ssh/id_rsa.pub 2>/dev/null || cat /root/.ssh/id_ed25519.pub 2>/dev/null) \
    || die "No SSH public key found in /root/.ssh/"

if $DRY_RUN; then
    log "[dry-run] Would render $CLOUD_INIT_TEMPLATE → $CLOUD_INIT_RENDERED"
    log "[dry-run] HOSTNAME=$RUNNER_ID, SSH_KEY=$SSH_PUBKEY"
else
    [[ -f "$CLOUD_INIT_TEMPLATE" ]] || die "Cloud-init template not found: $CLOUD_INIT_TEMPLATE"
    sed -e "s|\${HOSTNAME}|${RUNNER_ID}|g" \
        -e "s|\${SSH_KEY}|${SSH_PUBKEY}|g" \
        "$CLOUD_INIT_TEMPLATE" > "$CLOUD_INIT_RENDERED"
    log "Rendered cloud-init config to $CLOUD_INIT_RENDERED"
fi

# ---------------------------------------------------------------------------
# Step 1: Clone template and configure VM
# ---------------------------------------------------------------------------

log "=== Step 1: Creating VM $VMID from template $TEMPLATE_VMID ==="

run qm clone "$TEMPLATE_VMID" "$VMID" \
    --name "$RUNNER_ID" \
    --full true \
    --storage "$STORAGE"

run qm set "$VMID" \
    --ipconfig0 "ip=${IP}/24,gw=${GATEWAY}" \
    --nameserver "$NAMESERVER" \
    --cicustom "user=${CLOUD_INIT_SNIPPET}"

log "VM $VMID created and configured."

# ---------------------------------------------------------------------------
# Step 2: Start VM
# ---------------------------------------------------------------------------

log "=== Step 2: Starting VM $VMID ==="
run qm start "$VMID"
log "VM $VMID started."

# ---------------------------------------------------------------------------
# Step 3: Wait for VM to become SSH-reachable
# ---------------------------------------------------------------------------

log "=== Step 3: Waiting for VM to become reachable (timeout: ${BOOT_TIMEOUT}s) ==="

# Remove stale host key from a previous VM that used the same IP
ssh-keygen -R "$IP" 2>/dev/null || true

if ! $DRY_RUN; then
    elapsed=0
    while ! ssh $SSH_OPTS "$SSH_USER@$IP" true 2>/dev/null; do
        elapsed=$((elapsed + 5))
        if [[ $elapsed -ge $BOOT_TIMEOUT ]]; then
            die "VM did not become SSH-reachable within ${BOOT_TIMEOUT}s"
        fi
        sleep 5
        printf "."
    done
    echo ""
    log "VM is reachable via SSH."

    # Wait a bit more for cloud-init to finish
    log "Waiting for cloud-init to complete..."
    ssh $SSH_OPTS "$SSH_USER@$IP" \
        "cloud-init status --wait >/dev/null 2>&1 || sleep 10"
    log "Cloud-init done."

    # Set hostname to match runner-id
    log "Setting hostname to '$RUNNER_ID'..."
    ssh $SSH_OPTS "$SSH_USER@$IP" \
        "sudo hostnamectl set-hostname '${RUNNER_ID}'"
else
    log "[dry-run] Would wait for SSH on $SSH_USER@$IP"
fi

# ---------------------------------------------------------------------------
# Step 4: Register GitLab runner on the VM
# ---------------------------------------------------------------------------

log "=== Step 4: Registering GitLab runner ==="

if $DRY_RUN; then
    log "[dry-run] Would register GitLab runner on $SSH_USER@$IP"
    log "[dry-run] Would configure pre_build_script in config.toml"
else
    ssh $SSH_OPTS "$SSH_USER@$IP" \
        "sudo gitlab-runner register \
            --non-interactive \
            --url '${GITLAB_URL}' \
            --token '${GITLAB_TOKEN}' \
            --executor shell \
            --name '${RUNNER_ID}' && \
        sudo usermod -aG docker gitlab-runner"
    log "GitLab runner registered."

    # Add e2epool checkpoint hook to runner config (create only;
    # finalization is handled by the poller/webhook automatically)
    log "Configuring e2epool pre_build_script in config.toml..."
    ssh $SSH_OPTS "$SSH_USER@$IP" 'sudo python3 -' <<'PYSCRIPT'
path = "/etc/gitlab-runner/config.toml"
content = open(path).read()

pre = """export CHECKPOINT=$(e2epool create --job-id "$CI_JOB_ID") || { echo "Failed to create checkpoint"; exit 1; }
echo "Checkpoint created - $CHECKPOINT"
"""

insert = '  pre_build_script = """\n' + pre + '"""'
content = content.replace('executor = "shell"', 'executor = "shell"\n' + insert)

with open(path, "w") as f:
    f.write(content)
print("config.toml updated with e2epool hook")
PYSCRIPT
    log "Checkpoint hook configured."
fi

# ---------------------------------------------------------------------------
# Step 5: Register runner via e2epool admin API
# ---------------------------------------------------------------------------

log "=== Step 5: Registering runner '$RUNNER_ID' via e2epool API ==="

# Build the registration payload
REG_PAYLOAD=$(jq -n \
    --arg rid "$RUNNER_ID" \
    --arg host "$PROXMOX_HOST" \
    --arg user "$PROXMOX_USER" \
    --arg tname "$PROXMOX_TOKEN_NAME" \
    --arg tval "$PROXMOX_TOKEN_VALUE" \
    --arg node "$PROXMOX_NODE" \
    --argjson vmid "$VMID" \
    --argjson tags "$TAGS_JSON" \
    '{
        runner_id: $rid,
        backend: "proxmox",
        proxmox_host: $host,
        proxmox_user: $user,
        proxmox_token_name: $tname,
        proxmox_token_value: $tval,
        proxmox_node: $node,
        proxmox_vmid: $vmid,
        tags: $tags
    }')

# Add optional gitlab_runner_id if provided
if [[ -n "$GITLAB_RUNNER_ID" ]]; then
    REG_PAYLOAD=$(echo "$REG_PAYLOAD" | jq --argjson gid "$GITLAB_RUNNER_ID" '. + {gitlab_runner_id: $gid}')
fi

if $DRY_RUN; then
    log "[dry-run] Would POST to ${CONTROLLER_URL}/api/runners:"
    echo "$REG_PAYLOAD" | jq .
    RUNNER_TOKEN="dry-run-token-placeholder"
else
    RESPONSE=$(curl -s -w "\n%{http_code}" \
        -X POST "${CONTROLLER_URL}/api/runners" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$REG_PAYLOAD")

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [[ "$HTTP_CODE" != "201" ]]; then
        err "API registration failed (HTTP $HTTP_CODE):"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
        die "Cannot continue without a valid runner token."
    fi

    RUNNER_TOKEN=$(echo "$BODY" | jq -r '.token')
    if [[ -z "$RUNNER_TOKEN" || "$RUNNER_TOKEN" == "null" ]]; then
        die "API returned 201 but no token found in response."
    fi

    log "Runner registered. Token: ${RUNNER_TOKEN:0:8}..."
fi

# ---------------------------------------------------------------------------
# Step 6: Install e2epool agent on the VM
# ---------------------------------------------------------------------------

log "=== Step 6: Installing e2epool agent on VM ==="

remote_ssh() { ssh $SSH_OPTS "$SSH_USER@$IP" "$@"; }

if $DRY_RUN; then
    log "[dry-run] Would install e2epool agent on $SSH_USER@$IP"
    log "[dry-run] Agent config: controller_url=$WS_URL, runner_id=$RUNNER_ID, token=${RUNNER_TOKEN:0:8}..."
else
    # 6a: Install e2epool package in a venv (packages already installed by cloud-init)
    log "Installing e2epool package..."
    remote_ssh "sudo python3 -m venv /opt/e2epool/venv && \
        sudo /opt/e2epool/venv/bin/pip install 'git+${E2EPOOL_REPO}' && \
        sudo ln -sf /opt/e2epool/venv/bin/e2epool /usr/local/bin/e2epool"

    # 6b: Write agent config
    log "Writing agent config..."
    printf 'controller_url: "%s"\nrunner_id: "%s"\ntoken: "%s"\n' \
        "$WS_URL" "$RUNNER_ID" "$RUNNER_TOKEN" \
        | remote_ssh "sudo mkdir -p /etc/e2epool && sudo tee /etc/e2epool/agent.yml >/dev/null"

    # 6c: Write systemd service unit
    log "Installing systemd service..."
    printf '%s\n' \
        "[Unit]" \
        "Description=e2epool Agent" \
        "After=network-online.target" \
        "" \
        "[Service]" \
        "Type=simple" \
        "ExecStart=/usr/local/bin/e2epool agent" \
        "Restart=always" \
        "RestartSec=10" \
        "Environment=E2EPOOL_AGENT_CONFIG=/etc/e2epool/agent.yml" \
        "" \
        "[Install]" \
        "WantedBy=multi-user.target" \
        | remote_ssh "sudo tee /etc/systemd/system/e2epool-agent.service >/dev/null"

    # 6d: Enable and start the agent
    log "Starting e2epool-agent service..."
    remote_ssh "sudo systemctl daemon-reload && \
        sudo systemctl enable e2epool-agent && \
        sudo systemctl start e2epool-agent"

    # 6e: Verify
    sleep 2
    if remote_ssh "systemctl is-active --quiet e2epool-agent"; then
        log "e2epool-agent is running."
    else
        err "e2epool-agent failed to start. Check logs:"
        err "  ssh $SSH_USER@$IP 'journalctl -u e2epool-agent -n 20'"
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

log "=== Done ==="
log "VM:        $VMID ($RUNNER_ID)"
log "IP:        $IP"
log "Runner ID: $RUNNER_ID"
log "Agent:     connected to $WS_URL"
log ""
log "To check agent status:  ssh $SSH_USER@$IP 'systemctl status e2epool-agent'"
log "To view agent logs:     ssh $SSH_USER@$IP 'journalctl -u e2epool-agent -f'"
log "To deactivate runner:   curl -X DELETE ${CONTROLLER_URL}/api/runners/${RUNNER_ID} -H 'Authorization: Bearer <admin-token>'"
