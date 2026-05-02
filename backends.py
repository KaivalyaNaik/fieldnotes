import os
from typing import Protocol

from models import Alert, Deployment, LogLine


class DeploymentBackend(Protocol):
    def get_recent_deploys(self, service: str, limit: int) -> list[Deployment]: ...


class LogBackend(Protocol):
    def tail_logs(self, service: str, lines: int, since: str) -> list[LogLine]: ...


class AlertsBackend(Protocol):
    def check_alerts(
        self, severity: str | None, service: str | None
    ) -> list[Alert]: ...


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


def build_alerts_backend() -> AlertsBackend:
    name = os.environ.get("FIELDNOTES_ALERT_BACKEND", "alertmanager")
    if name == "alertmanager":
        from alertmanager import AlertmanagerBackend

        return AlertmanagerBackend()
    raise ValueError(
        f"Unknown FIELDNOTES_ALERT_BACKEND={name!r}. Supported: 'alertmanager'."
    )
