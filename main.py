from mcp.server.fastmcp import FastMCP

from backends import DeploymentBackend, build_deployment_backend
from models import Deployment

mcp = FastMCP("fieldnotes")

_LIMIT_MAX = 50
_backend_singleton: DeploymentBackend | None = None


def _backend() -> DeploymentBackend:
    global _backend_singleton
    if _backend_singleton is None:
        _backend_singleton = build_deployment_backend()
    return _backend_singleton


@mcp.tool()
def get_recent_deploys(service: str, limit: int = 10) -> list[Deployment]:
    """Return recent deploys for a service, newest first.

    `service` is a service identifier configured by your operator (e.g.
    "payments", "api"). If no service map is configured, `service` is
    forwarded verbatim as the workflow stem. `limit` must be in [1, 50];
    out-of-range values raise ValueError.
    """
    if not 1 <= limit <= _LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {_LIMIT_MAX}, got {limit}.")
    return _backend().get_recent_deploys(service, limit)


if __name__ == "__main__":
    mcp.run()
