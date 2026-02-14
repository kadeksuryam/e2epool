import time

from proxmoxer import ProxmoxAPI

from e2epool.backends.agent_rpc import run_on_agent, wait_for_agent
from e2epool.inventory import RunnerConfig


class ProxmoxBackend:
    def create_checkpoint(self, runner: RunnerConfig, name: str) -> None:
        pve = self._get_pve(runner)
        node = pve.nodes(runner.proxmox_node)
        node.qemu(runner.proxmox_vmid).snapshot.create(
            snapname=name, description=f"e2epool checkpoint {name}"
        )

    def reset(self, runner: RunnerConfig, name: str) -> None:
        """Stop VM, rollback to snapshot, start VM, delete snapshot."""
        pve = self._get_pve(runner)
        node = pve.nodes(runner.proxmox_node)
        vm = node.qemu(runner.proxmox_vmid)

        vm.status.stop.create()
        self._wait_for_status(vm, "stopped")

        upid = vm.snapshot(name).rollback.create()
        self._wait_for_task(node, upid)

        vm.status.start.create()
        self._wait_for_status(vm, "running", timeout=180)

        # Wait for agent to reconnect after VM boot
        wait_for_agent(runner.runner_id)

        if runner.cleanup_cmd:
            run_on_agent(runner.runner_id, runner.cleanup_cmd)

        vm.snapshot(name).delete()

    def cleanup(self, runner: RunnerConfig, name: str) -> None:
        """Success path: run cleanup command if present, then delete snapshot."""
        pve = self._get_pve(runner)
        vm = pve.nodes(runner.proxmox_node).qemu(runner.proxmox_vmid)

        if runner.cleanup_cmd:
            run_on_agent(runner.runner_id, runner.cleanup_cmd)

        vm.snapshot(name).delete()

    def check_ready(self, runner: RunnerConfig) -> bool:
        """Wait for agent to connect (replaces SSH polling)."""
        return wait_for_agent(runner.runner_id)

    def _get_pve(self, runner: RunnerConfig) -> ProxmoxAPI:
        return ProxmoxAPI(
            runner.proxmox_host,
            user=runner.proxmox_user,
            token_name=runner.proxmox_token_name,
            token_value=runner.proxmox_token_value,
            verify_ssl=False,
        )

    def _wait_for_status(self, vm, target: str, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = vm.status.current.get()
            if status.get("status") == target:
                return
            time.sleep(2)
        raise TimeoutError(f"VM did not reach '{target}' within {timeout}s")

    def _wait_for_task(self, node, upid: str, timeout: int = 120) -> None:
        """Wait for a Proxmox task to complete."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = node.tasks(upid).status.get()
            if task.get("status") == "stopped":
                if task.get("exitstatus") != "OK":
                    raise RuntimeError(
                        f"Proxmox task failed: {task.get('exitstatus')}"
                    )
                return
            time.sleep(2)
        raise TimeoutError(f"Proxmox task did not complete within {timeout}s")
