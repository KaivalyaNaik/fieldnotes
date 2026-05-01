"""GitHub Actions deploy backend.

Maps a `service` arg to a workflow filename: service="api" reads
.github/workflows/api.yml from $GITHUB_REPO. Requires GITHUB_TOKEN with
`actions:read` and GITHUB_REPO in `owner/repo` form.
"""

import os

import httpx

from models import Deployment, DeployStatus

_API = "https://api.github.com"


class GitHubActionsBackend:
    def __init__(self) -> None:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        repo = os.environ.get("GITHUB_REPO", "").strip()
        if not token:
            raise RuntimeError(
                "GITHUB_TOKEN is required for the GitHub Actions deploy backend."
            )
        if not repo or "/" not in repo:
            raise RuntimeError(
                "GITHUB_REPO must be set to 'owner/repo' for the GitHub Actions deploy backend."
            )
        self._repo = repo
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )

    def get_recent_deploys(self, service: str, limit: int) -> list[Deployment]:
        url = f"{_API}/repos/{self._repo}/actions/workflows/{service}.yml/runs"
        resp = self._client.get(url, params={"per_page": limit})
        resp.raise_for_status()
        runs = resp.json().get("workflow_runs", [])
        return [_to_deployment(run) for run in runs]


def _to_deployment(run: dict) -> Deployment:
    actor = run.get("actor") or {}
    return Deployment(
        id=str(run["id"]),
        status=_map_status(run.get("status"), run.get("conclusion")),
        actor=actor.get("login", "unknown"),
        timestamp=run["run_started_at"],
        commit_sha=run["head_sha"],
        url=run.get("html_url"),
    )


def _map_status(status: str | None, conclusion: str | None) -> DeployStatus:
    if conclusion == "success":
        return "success"
    if conclusion in ("failure", "timed_out", "startup_failure"):
        return "failure"
    if conclusion == "cancelled":
        return "cancelled"
    if status == "in_progress":
        return "in_progress"
    return "pending"
