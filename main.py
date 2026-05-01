from mcp.server.fastmcp import FastMCP

from backends import build_deployment_backend
from models import Deployment

mcp = FastMCP("fieldnotes")
backend = build_deployment_backend()


@mcp.tool()
def get_recent_deploys(service: str, limit: int = 10) -> list[Deployment]:
    """Return recent deploys for a service, newest first."""
    limit = max(1, min(limit, 50))
    return backend.get_recent_deploys(service, limit)


if __name__ == "__main__":
    mcp.run()
