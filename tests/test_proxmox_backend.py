from unittest.mock import MagicMock, patch

import pytest

from e2epool.backends.proxmox import ProxmoxBackend


@patch("e2epool.backends.proxmox.ProxmoxAPI")
def test_create_checkpoint_calls_pve_snapshot_create(mock_proxmox_api, mock_runner):
    """Verify that create_checkpoint calls the Proxmox snapshot.create API."""
    mock_pve = MagicMock()
    mock_proxmox_api.return_value = mock_pve
    mock_node = MagicMock()
    mock_pve.nodes.return_value = mock_node
    mock_qemu = MagicMock()
    mock_node.qemu.return_value = mock_qemu
    mock_snapshot = MagicMock()
    mock_qemu.snapshot = mock_snapshot

    backend = ProxmoxBackend()
    backend.create_checkpoint(mock_runner, "test-checkpoint")

    mock_pve.nodes.assert_called_once_with(mock_runner.proxmox_node)
    mock_node.qemu.assert_called_once_with(mock_runner.proxmox_vmid)
    mock_snapshot.create.assert_called_once_with(
        snapname="test-checkpoint",
        description="e2epool checkpoint test-checkpoint",
    )


@patch("e2epool.backends.proxmox.wait_for_agent")
@patch("e2epool.backends.proxmox.ProxmoxAPI")
def test_reset_stops_rollbacks_starts_deletes(mock_proxmox_api, mock_wait, mock_runner):
    """Verify reset sequence: stop, rollback, start, wait for agent, delete snapshot."""
    mock_runner.cleanup_cmd = None

    mock_pve = MagicMock()
    mock_proxmox_api.return_value = mock_pve
    mock_node = MagicMock()
    mock_pve.nodes.return_value = mock_node
    mock_vm = MagicMock()
    mock_node.qemu.return_value = mock_vm

    mock_stop = MagicMock()
    mock_vm.status.stop = mock_stop
    mock_start = MagicMock()
    mock_vm.status.start = mock_start

    mock_snapshot_obj = MagicMock()
    mock_vm.snapshot.return_value = mock_snapshot_obj

    mock_wait.return_value = True

    backend = ProxmoxBackend()

    with patch.object(backend, "_wait_for_status"):
        backend.reset(mock_runner, "test-checkpoint")

    mock_stop.create.assert_called_once()
    mock_snapshot_obj.rollback.create.assert_called_once()
    mock_start.create.assert_called_once()
    mock_wait.assert_called_once_with(mock_runner.runner_id)
    mock_snapshot_obj.delete.assert_called_once()


@patch("e2epool.backends.proxmox.run_on_agent")
@patch("e2epool.backends.proxmox.wait_for_agent")
@patch("e2epool.backends.proxmox.ProxmoxAPI")
def test_reset_with_cleanup_runs_agent_cmd(
    mock_proxmox_api, mock_wait, mock_run, mock_runner
):
    """Verify reset with cleanup_cmd runs command via agent before snapshot delete."""
    mock_runner.cleanup_cmd = "cleanup.sh"

    mock_pve = MagicMock()
    mock_proxmox_api.return_value = mock_pve
    mock_node = MagicMock()
    mock_pve.nodes.return_value = mock_node
    mock_vm = MagicMock()
    mock_node.qemu.return_value = mock_vm

    mock_snapshot_obj = MagicMock()
    mock_vm.snapshot.return_value = mock_snapshot_obj

    mock_wait.return_value = True
    mock_run.return_value = ""

    backend = ProxmoxBackend()

    with patch.object(backend, "_wait_for_status"):
        backend.reset(mock_runner, "test-checkpoint")

    mock_run.assert_called_once_with(mock_runner.runner_id, "cleanup.sh")
    mock_snapshot_obj.delete.assert_called_once()


@patch("e2epool.backends.proxmox.wait_for_agent")
def test_check_ready_waits_for_agent(mock_wait, mock_runner):
    """Verify check_ready waits for agent connection."""
    mock_wait.return_value = True

    backend = ProxmoxBackend()
    result = backend.check_ready(mock_runner)

    assert result is True
    mock_wait.assert_called_once_with(mock_runner.runner_id)


@patch("e2epool.backends.proxmox.wait_for_agent")
def test_check_ready_timeout_raises(mock_wait, mock_runner):
    """Verify check_ready raises TimeoutError when agent doesn't connect."""
    mock_wait.side_effect = TimeoutError("Agent not connected after 120s")

    backend = ProxmoxBackend()

    with pytest.raises(TimeoutError) as exc_info:
        backend.check_ready(mock_runner)

    assert "not connected" in str(exc_info.value)


@patch("e2epool.backends.proxmox.run_on_agent")
@patch("e2epool.backends.proxmox.ProxmoxAPI")
def test_cleanup_runs_agent_cmd(mock_proxmox_api, mock_run, mock_runner):
    """Verify cleanup runs cleanup_cmd via agent."""
    mock_runner.cleanup_cmd = "cleanup.sh"

    mock_pve = MagicMock()
    mock_proxmox_api.return_value = mock_pve
    mock_node = MagicMock()
    mock_pve.nodes.return_value = mock_node
    mock_vm = MagicMock()
    mock_node.qemu.return_value = mock_vm
    mock_snapshot_obj = MagicMock()
    mock_vm.snapshot.return_value = mock_snapshot_obj

    mock_run.return_value = ""

    backend = ProxmoxBackend()
    backend.cleanup(mock_runner, "test-checkpoint")

    mock_run.assert_called_once_with(mock_runner.runner_id, "cleanup.sh")
    mock_snapshot_obj.delete.assert_called_once()
