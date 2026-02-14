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
    def __init__(self, base_url: str, token: str, project_id: int | None = None):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._project_id = project_id

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._token}

    def get_job_status(self, job_id: str) -> str:
        """Query GitLab Jobs API and return normalized status."""
        if self._project_id:
            url = f"{self._base_url}/api/v4/projects/{self._project_id}/jobs/{job_id}"
        else:
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
