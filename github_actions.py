"""GitHub Actions deploy backend.

Maps a `service` arg to a workflow filename: service="api" reads
.github/workflows/api.yml from $GITHUB_REPO. Requires GITHUB_TOKEN with
`actions:read` and GITHUB_REPO in `owner/repo` form.

If FIELDNOTES_SERVICE_MAP (or FIELDNOTES_SERVICE_MAP_FILE) is set, the
caller-supplied `service` is resolved through it to a workflow stem first.
"""

import atexit
import json
import os
import re
from pathlib import Path

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
        self._service_map = _load_service_map()
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
        stem = _resolve_workflow_stem(service, self._service_map)
        if not _SERVICE_RE.fullmatch(stem):
            raise ValueError(
                f"Invalid workflow stem {stem!r}: must match [A-Za-z0-9_.-]+ "
                f"(maps to .github/workflows/{stem}.yml)."
            )
        url = f"{_API}/repos/{self._repo}/actions/workflows/{stem}.yml/runs"
        try:
            resp = self._client.get(url, params={"per_page": limit})
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                _friendly_http_error(e, service, stem, self._repo)
            ) from e
        runs = resp.json().get("workflow_runs", [])
        return [_to_deployment(run) for run in runs]


# Empty/whitespace-only env values are treated as unset — operators commonly
# leave shell vars defined but blank.
def _load_service_map() -> dict[str, str]:
    raw_env = os.environ.get("FIELDNOTES_SERVICE_MAP", "").strip()
    raw_file = os.environ.get("FIELDNOTES_SERVICE_MAP_FILE", "").strip()
    if raw_env and raw_file:
        raise RuntimeError(
            "set FIELDNOTES_SERVICE_MAP or FIELDNOTES_SERVICE_MAP_FILE, not both"
        )
    if raw_env:
        return _parse_service_map_env(raw_env)
    if raw_file:
        return _load_service_map_file(raw_file)
    return {}


def _parse_service_map_env(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise RuntimeError(f"FIELDNOTES_SERVICE_MAP entry {pair!r} missing '='")
        key, _, val = pair.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            raise RuntimeError(f"FIELDNOTES_SERVICE_MAP entry {pair!r}: empty key")
        if not val:
            raise RuntimeError(f"FIELDNOTES_SERVICE_MAP entry {pair!r}: empty value")
        if not _SERVICE_RE.fullmatch(val):
            raise RuntimeError(
                f"FIELDNOTES_SERVICE_MAP entry {pair!r}: workflow stem {val!r} "
                f"must match [A-Za-z0-9_.-]+"
            )
        if key in out:
            raise RuntimeError(f"FIELDNOTES_SERVICE_MAP duplicate key {key!r}")
        out[key] = val
    return out


def _load_service_map_file(path_str: str) -> dict[str, str]:
    path = Path(path_str)
    prefix = f"FIELDNOTES_SERVICE_MAP_FILE={path_str}"
    if path.suffix != ".json":
        raise RuntimeError(f"{prefix}: only .json files are supported")
    try:
        text = path.read_text()
    except OSError as e:
        raise RuntimeError(f"{prefix}: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{prefix}: invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(
            f"{prefix}: must contain a flat object of string→string"
        )
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str) or not k or not v:
            raise RuntimeError(
                f"{prefix}: must contain a flat object of string→string"
            )
        if not _SERVICE_RE.fullmatch(v):
            raise RuntimeError(
                f"{prefix}: workflow stem {v!r} for {k!r} "
                f"must match [A-Za-z0-9_.-]+"
            )
    return data


def _resolve_workflow_stem(service: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return service
    if service in mapping:
        return mapping[service]
    raise ValueError(
        f"unknown service {service!r}. known: {', '.join(sorted(mapping))}"
    )


def _friendly_http_error(
    err: httpx.HTTPStatusError, logical: str, stem: str, repo: str
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
        if logical != stem:
            return (
                f"GitHub returned 404 — service {logical!r} resolved to workflow "
                f"{stem}.yml, which was not found in {repo} "
                "(or repo does not exist / token lacks access)."
            )
        return (
            f"GitHub returned 404 — workflow {stem}.yml not found in {repo}, "
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
