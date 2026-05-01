"""GitHub Actions deploy backend.

Maps a `service` arg to a workflow filename: service="api" reads
.github/workflows/api.yml from $GITHUB_REPO. Requires GITHUB_TOKEN with
`actions:read` and GITHUB_REPO in `owner/repo` form.
"""

import atexit
import os
import re

import httpx

from models import Deployment, DeployStatus

_API = "https://api.github.com"
_SERVICE_RE = re.compile(r"[A-Za-z0-9_.-]+")


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
        atexit.register(self._client.close)

    def get_recent_deploys(self, service: str, limit: int) -> list[Deployment]:
        if not _SERVICE_RE.fullmatch(service):
            raise ValueError(
                f"Invalid service name {service!r}: must match [A-Za-z0-9_.-]+ "
                f"(maps to .github/workflows/{service}.yml)."
            )
        url = f"{_API}/repos/{self._repo}/actions/workflows/{service}.yml/runs"
        try:
            resp = self._client.get(url, params={"per_page": limit})
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(_friendly_http_error(e, service, self._repo)) from e
        runs = resp.json().get("workflow_runs", [])
        return [_to_deployment(run) for run in runs]


def _friendly_http_error(
    err: httpx.HTTPStatusError, service: str, repo: str
) -> str:
    code = err.response.status_code
    if code == 401:
        return "GitHub returned 401 Unauthorized — check that GITHUB_TOKEN is valid and not expired."
    if code == 403:
        if err.response.headers.get("X-RateLimit-Remaining") == "0":
            reset = err.response.headers.get("X-RateLimit-Reset", "?")
            return f"GitHub rate limit exceeded (resets at epoch {reset})."
        return f"GitHub returned 403 Forbidden — token may lack `actions:read` on {repo}."
    if code == 404:
        return (
            f"GitHub returned 404 — workflow {service}.yml not found in {repo}, "
            "or repo does not exist / token lacks access."
        )
    if code == 429:
        return "GitHub rate limit exceeded (429). Wait and retry."
    return f"GitHub API error {code}: {err.response.text[:200]}"


def _to_deployment(run: dict) -> Deployment:
    actor = run.get("actor") or {}
    return Deployment(
        id=str(run.get("id", "")),
        status=_map_status(run.get("status"), run.get("conclusion")),
        actor=actor.get("login", "unknown"),
        timestamp=run.get("run_started_at"),
        commit_sha=run.get("head_sha", ""),
        url=run.get("html_url"),
    )


def _map_status(status: str | None, conclusion: str | None) -> DeployStatus:
    if conclusion == "success":
        return "success"
    if conclusion in ("failure", "timed_out", "startup_failure"):
        return "failure"
    if conclusion == "cancelled":
        return "cancelled"
    if conclusion in ("action_required", "neutral", "skipped", "stale"):
        return conclusion
    if status == "in_progress":
        return "in_progress"
    if status == "queued":
        return "pending"
    return "unknown"
