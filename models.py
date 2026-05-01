from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DeployStatus = Literal[
    "success",
    "failure",
    "in_progress",
    "cancelled",
    "pending",
    "action_required",
    "neutral",
    "skipped",
    "stale",
    "unknown",
]


class Deployment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    status: DeployStatus
    actor: str
    timestamp: datetime
    commit_sha: str
    url: str | None = None
