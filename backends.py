import os
from typing import Protocol

from models import Deployment


class DeploymentBackend(Protocol):
    def get_recent_deploys(self, service: str, limit: int) -> list[Deployment]: ...


def build_deployment_backend() -> DeploymentBackend:
    name = os.environ.get("FIELDNOTES_DEPLOY_BACKEND", "github")
    if name == "github":
        from github_actions import GitHubActionsBackend

        return GitHubActionsBackend()
    raise ValueError(
        f"Unknown FIELDNOTES_DEPLOY_BACKEND={name!r}. Supported: 'github'."
    )
