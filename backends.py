import os
from typing import Protocol

from models import Deployment, LogLine


class DeploymentBackend(Protocol):
    def get_recent_deploys(self, service: str, limit: int) -> list[Deployment]: ...


class LogBackend(Protocol):
    def tail_logs(self, service: str, lines: int, since: str) -> list[LogLine]: ...


def build_deployment_backend() -> DeploymentBackend:
    name = os.environ.get("FIELDNOTES_DEPLOY_BACKEND", "github")
    if name == "github":
        from github_actions import GitHubActionsBackend

        return GitHubActionsBackend()
    raise ValueError(
        f"Unknown FIELDNOTES_DEPLOY_BACKEND={name!r}. Supported: 'github'."
    )


def build_log_backend() -> LogBackend:
    name = os.environ.get("FIELDNOTES_LOG_BACKEND", "loki")
    if name == "loki":
        from loki import LokiBackend

        return LokiBackend()
    raise ValueError(f"Unknown FIELDNOTES_LOG_BACKEND={name!r}. Supported: 'loki'.")
