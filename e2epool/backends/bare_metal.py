from e2epool.backends.agent_rpc import run_on_agent, wait_for_agent
from e2epool.inventory import RunnerConfig


class BareMetalBackend:
    def create_checkpoint(self, runner: RunnerConfig, name: str) -> None:
        """No-op for bare metal — no snapshot capability."""
        pass

    def reset(self, runner: RunnerConfig, name: str) -> None:
        """Run the reset_cmd via agent."""
        if runner.reset_cmd:
            run_on_agent(runner.runner_id, runner.reset_cmd)

    def cleanup(self, runner: RunnerConfig, name: str) -> None:
        """Run cleanup_cmd via agent if configured."""
        if runner.cleanup_cmd:
            run_on_agent(runner.runner_id, runner.cleanup_cmd)

    def check_ready(self, runner: RunnerConfig) -> bool:
        """Check readiness via agent command or connectivity."""
        if runner.readiness_cmd:
            try:
                run_on_agent(runner.runner_id, runner.readiness_cmd)
                return True
            except Exception:
                return False

        try:
            return wait_for_agent(runner.runner_id, timeout=5)
        except TimeoutError:
            return False
