"""Grafana Loki log backend.

Resolves a `service` arg to a Loki label value and queries `/loki/api/v1/query_range`.
The label name defaults to `service` and is overridable via FIELDNOTES_LOKI_LABEL.

Auth follows Grafana Cloud convention: HTTP Basic with LOKI_USER_ID:LOKI_API_KEY.
LOKI_URL is required (e.g. `https://logs-prod-006.grafana.net`).

If FIELDNOTES_LOG_SERVICE_MAP (or FIELDNOTES_LOG_SERVICE_MAP_FILE) is set, the
caller-supplied `service` is resolved through it to a Loki label value first.
"""

import atexit
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from models import LogLevel, LogLine, parse_since
from service_map import load_flat_map, resolve_service

# Prometheus / Loki label name grammar.
_LABEL_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
# Conservative subset of Loki label values: rules out the LogQL injection
# surface (quotes, braces, backslashes) while permitting realistic identifiers.
_LABEL_VALUE_RE = re.compile(r"[A-Za-z0-9_.\-/]+")

_LEVELS: dict[str, LogLevel] = {
    "debug": "debug",
    "trace": "debug",
    "info": "info",
    "informational": "info",
    "notice": "info",
    "warn": "warn",
    "warning": "warn",
    "error": "error",
    "err": "error",
    "critical": "fatal",
    "crit": "fatal",
    "fatal": "fatal",
    "emerg": "fatal",
    "alert": "fatal",
}


def _normalize_level(raw: str | None) -> LogLevel:
    if not raw:
        return "unknown"
    return _LEVELS.get(raw.strip().lower(), "unknown")


def _validate_label_value(value: str) -> None:
    if not _LABEL_VALUE_RE.fullmatch(value):
        raise ValueError(
            f"Loki label value {value!r} must match {_LABEL_VALUE_RE.pattern}"
        )


class LokiBackend:
    def __init__(self) -> None:
        url = os.environ.get("LOKI_URL", "").strip().rstrip("/")
        user = os.environ.get("LOKI_USER_ID", "").strip()
        key = os.environ.get("LOKI_API_KEY", "").strip()
        if not url:
            raise RuntimeError("LOKI_URL is required for the Loki log backend.")
        if not user or not key:
            raise RuntimeError(
                "LOKI_USER_ID and LOKI_API_KEY are required for the Loki log backend."
            )
        label = os.environ.get("FIELDNOTES_LOKI_LABEL", "service").strip() or "service"
        if not _LABEL_NAME_RE.fullmatch(label):
            raise RuntimeError(
                f"FIELDNOTES_LOKI_LABEL={label!r} must match {_LABEL_NAME_RE.pattern}."
            )
        self._url = url
        self._label = label
        self._service_map = load_flat_map(
            "FIELDNOTES_LOG_SERVICE_MAP",
            "FIELDNOTES_LOG_SERVICE_MAP_FILE",
            _validate_label_value,
        )
        self._client = httpx.Client(auth=(user, key), timeout=10.0)
        atexit.register(self._client.close)

    def tail_logs(self, service: str, lines: int, since: str) -> list[LogLine]:
        value = resolve_service(self._service_map, service)
        # Defense-in-depth: mapped values are validated at load time, so this
        # mostly guards the no-map pass-through case.
        if not _LABEL_VALUE_RE.fullmatch(value):
            raise ValueError(
                f"Invalid Loki label value {value!r}: must match "
                f"{_LABEL_VALUE_RE.pattern}."
            )
        end = datetime.now(timezone.utc)
        start = end - parse_since(since)
        params = {
            "query": f'{{{self._label}="{value}"}}',
            "start": str(int(start.timestamp() * 1_000_000_000)),
            "end": str(int(end.timestamp() * 1_000_000_000)),
            "limit": str(lines),
            "direction": "backward",
        }
        try:
            resp = self._client.get(
                f"{self._url}/loki/api/v1/query_range", params=params
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(_friendly_loki_error(e)) from e
        return _flatten_streams(resp.json(), lines)


def _flatten_streams(payload: dict[str, Any], lines: int) -> list[LogLine]:
    result = (payload.get("data") or {}).get("result") or []
    out: list[LogLine] = []
    for stream in result:
        labels = dict(stream.get("stream") or {})
        level = _normalize_level(labels.get("level") or labels.get("detected_level"))
        for row in stream.get("values") or []:
            ns_str, message = row[0], row[1]
            ts = datetime.fromtimestamp(
                int(ns_str) / 1_000_000_000, tz=timezone.utc
            )
            out.append(
                LogLine(timestamp=ts, message=message, level=level, labels=labels)
            )
    out.sort(key=lambda r: r.timestamp, reverse=True)
    return out[:lines]


def _friendly_loki_error(err: httpx.HTTPStatusError) -> str:
    code = err.response.status_code
    if code == 401:
        return "Loki returned 401 — check LOKI_USER_ID and LOKI_API_KEY."
    if code == 400:
        return f"Loki rejected the query (400): {err.response.text[:200]}"
    if code == 429:
        return "Loki rate limit exceeded (429). Wait and retry."
    if code in (502, 503, 504):
        return f"Loki upstream error {code}. Try again shortly."
    return f"Loki API error {code}: {err.response.text[:200]}"
