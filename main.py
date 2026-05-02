from mcp.server.fastmcp import FastMCP

from backends import (
    AlertsBackend,
    DeploymentBackend,
    LogBackend,
    build_alerts_backend,
    build_deployment_backend,
    build_log_backend,
)
from models import Alert, Deployment, LogLine, parse_since

mcp = FastMCP("fieldnotes")

_LIMIT_MAX = 50
_LINES_MAX = 500
_ALERT_SEVERITIES = ("critical", "error", "warning", "info")
_deploy_backend_singleton: DeploymentBackend | None = None
_log_backend_singleton: LogBackend | None = None
_alerts_backend_singleton: AlertsBackend | None = None


def _deploy_backend() -> DeploymentBackend:
    global _deploy_backend_singleton
    if _deploy_backend_singleton is None:
        _deploy_backend_singleton = build_deployment_backend()
    return _deploy_backend_singleton


def _log_backend() -> LogBackend:
    global _log_backend_singleton
    if _log_backend_singleton is None:
        _log_backend_singleton = build_log_backend()
    return _log_backend_singleton


def _alerts_backend() -> AlertsBackend:
    global _alerts_backend_singleton
    if _alerts_backend_singleton is None:
        _alerts_backend_singleton = build_alerts_backend()
    return _alerts_backend_singleton


@mcp.tool()
def get_recent_deploys(service: str, limit: int = 10) -> list[Deployment]:
    """Return recent deploys for a service, newest first.

    `service` is a service identifier configured by your operator (e.g.
    "payments", "api"). If no service map is configured, `service` is
    forwarded verbatim. `limit` must be in [1, 50]; out-of-range values
    raise ValueError.
    """
    if not 1 <= limit <= _LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {_LIMIT_MAX}, got {limit}.")
    return _deploy_backend().get_recent_deploys(service, limit)


@mcp.tool()
def tail_logs(service: str, lines: int = 100, since: str = "15m") -> list[LogLine]:
    """Return recent log lines for a service, newest first.

    `service` is a service identifier configured by your operator. `lines`
    must be in [1, 500]; `since` is a relative window like "15m", "1h",
    "24h", "2d". Out-of-range or malformed values raise ValueError.
    """
    if not 1 <= lines <= _LINES_MAX:
        raise ValueError(f"lines must be between 1 and {_LINES_MAX}, got {lines}.")
    parse_since(since)
    return _log_backend().tail_logs(service, lines, since)


@mcp.tool()
def check_alerts(
    severity: str | None = None, service: str | None = None
) -> list[Alert]:
    """Return alerts currently firing, optionally filtered by severity or service.

    `severity` is one of "critical", "error", "warning", "info" (or omitted).
    `service` is a service identifier configured by your operator; if no
    service map is configured, `service` is forwarded verbatim.
    """
    if severity is not None and severity not in _ALERT_SEVERITIES:
        raise ValueError(
            f"severity must be one of {_ALERT_SEVERITIES}, got {severity!r}."
        )
    return _alerts_backend().check_alerts(severity, service)


if __name__ == "__main__":
    mcp.run()
