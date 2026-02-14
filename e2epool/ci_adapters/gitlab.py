import httpx

from e2epool.config import settings

STATUS_MAP = {
    "running": "running",
    "success": "success",
    "failed": "failure",
    "canceled": "canceled",
    "manual": "running",
    "pending": "running",
    "created": "running",
}


class GitLabAdapter:
    def __init__(self):
        self._base_url = (settings.gitlab_url or "").rstrip("/")
        self._token = settings.gitlab_token or ""

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._token}

    def get_job_status(self, job_id: str) -> str:
        """Query GitLab Jobs API and return normalized status."""
        url = f"{self._base_url}/api/v4/jobs/{job_id}"
        resp = httpx.get(url, headers=self._headers(), timeout=settings.httpx_timeout)
        if resp.status_code == 404:
            raise ValueError(f"Job {job_id} not found")
        resp.raise_for_status()
        gitlab_status = resp.json()["status"]
        return STATUS_MAP.get(gitlab_status, "running")

    def pause_runner(self, runner_id: int) -> None:
        resp = httpx.put(
            f"{self._base_url}/api/v4/runners/{runner_id}",
            headers=self._headers(),
            json={"active": False},
            timeout=settings.httpx_timeout,
        )
        if resp.status_code == 404:
            raise ValueError(f"Runner {runner_id} not found")
        resp.raise_for_status()

    def unpause_runner(self, runner_id: int) -> None:
        resp = httpx.put(
            f"{self._base_url}/api/v4/runners/{runner_id}",
            headers=self._headers(),
            json={"active": True},
            timeout=settings.httpx_timeout,
        )
        if resp.status_code == 404:
            raise ValueError(f"Runner {runner_id} not found")
        resp.raise_for_status()
