import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


LogLevel = Literal["debug", "info", "warn", "error", "fatal", "unknown"]


class LogLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    message: str
    level: LogLevel = "unknown"
    labels: dict[str, str] = Field(default_factory=dict)


_SINCE_RE = re.compile(r"^(\d+)([smhd])$")
_SINCE_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_since(s: str) -> timedelta:
    """Parse a relative window like "15m", "1h", "24h", "2d" into a
    timedelta. Raises `ValueError` on malformed input or non-positive
    durations.
    """
    m = _SINCE_RE.match(s)
    if not m:
        raise ValueError(
            f"since must look like '15m', '1h', '24h', '2d' (got {s!r})."
        )
    n = int(m.group(1))
    if n <= 0:
        raise ValueError(f"since must be positive (got {s!r}).")
    return timedelta(**{_SINCE_UNITS[m.group(2)]: n})
