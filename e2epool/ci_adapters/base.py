from typing import Protocol


class CIAdapterProtocol(Protocol):
    def get_job_status(self, job_id: str) -> str:
        """Return normalized status: 'running', 'success', 'failure', 'canceled'."""
        ...

    def pause_runner(self, runner_id: int) -> None: ...
    def unpause_runner(self, runner_id: int) -> None: ...
