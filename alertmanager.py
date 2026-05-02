"""Prometheus Alertmanager alerts backend.

Resolves a `service` arg to a label-matcher value and queries
`/api/v2/alerts` with `?active=true&silenced=false&inhibited=false` so only
firing alerts surface. The label name defaults to `service` and is
overridable via FIELDNOTES_ALERTMANAGER_LABEL.

Auth is HTTP Basic via ALERTMANAGER_USER_ID / ALERTMANAGER_API_KEY (both or
neither). ALERTMANAGER_URL is required.

If FIELDNOTES_ALERT_SERVICE_MAP (or FIELDNOTES_ALERT_SERVICE_MAP_FILE) is
set, the caller-supplied `service` is resolved through it to a label value
first.

Severity is normalized to a canonical four (`critical`, `error`, `warning`,
`info`) plus `unknown` — vendor synonyms collapse here so MCP consumers
don't need a vocabulary table. Notably `high → error`, `low → info`, and
`page → critical`: an operator who tags a rule `severity: high` will see it
surface as `error`. This is opinion, not bug — change `_SEVERITIES` if your
shop tags differently.
"""

import atexit
import os
import re
from datetime import datetime
from typing import Any

import httpx

from models import Alert, AlertSeverity
from service_map import load_flat_map, resolve_service

# Prometheus label-name grammar (mirrors loki.py — kept as a local copy so
# the modules stay independent).
_LABEL_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
# Conservative subset of label values: rules out the matcher-injection
# surface (quotes, braces, backslashes) while permitting realistic identifiers.
_LABEL_VALUE_RE = re.compile(r"[A-Za-z0-9_.\-/]+")

_SEVERITIES: dict[str, AlertSeverity] = {
    "critical": "critical",
    "crit": "critical",
    "page": "critical",
    "error": "error",
    "err": "error",
    "high": "error",
    "warning": "warning",
    "warn": "warning",
    "medium": "warning",
    "info": "info",
    "informational": "info",
    "notice": "info",
    "low": "info",
}

# The set of values the backend will accept as a *filter*. `unknown` is an
# output bucket only — letting it through here would build a query that
# matches nothing.
_FILTERABLE_SEVERITIES = ("critical", "error", "warning", "info")


def _normalize_severity(raw: str | None) -> AlertSeverity:
    if not raw:
        return "unknown"
    return _SEVERITIES.get(raw.strip().lower(), "unknown")


def _validate_label_value(value: str) -> None:
    if not _LABEL_VALUE_RE.fullmatch(value):
        raise ValueError(
            f"Alertmanager label value {value!r} must match {_LABEL_VALUE_RE.pattern}"
        )


class AlertmanagerBackend:
    def __init__(self) -> None:
        url = os.environ.get("ALERTMANAGER_URL", "").strip().rstrip("/")
        user = os.environ.get("ALERTMANAGER_USER_ID", "").strip()
        key = os.environ.get("ALERTMANAGER_API_KEY", "").strip()
        if not url:
            raise RuntimeError(
                "ALERTMANAGER_URL is required for the Alertmanager alerts backend."
            )
        if bool(user) != bool(key):
            raise RuntimeError(
                "ALERTMANAGER_USER_ID and ALERTMANAGER_API_KEY must be set together "
                "(or both unset for an unauthenticated Alertmanager)."
            )
        label = (
            os.environ.get("FIELDNOTES_ALERTMANAGER_LABEL", "service").strip()
            or "service"
        )
        if not _LABEL_NAME_RE.fullmatch(label):
            raise RuntimeError(
                f"FIELDNOTES_ALERTMANAGER_LABEL={label!r} must match "
                f"{_LABEL_NAME_RE.pattern}."
            )
        self._url = url
        self._label = label
        self._service_map = load_flat_map(
            "FIELDNOTES_ALERT_SERVICE_MAP",
            "FIELDNOTES_ALERT_SERVICE_MAP_FILE",
            _validate_label_value,
        )
        auth = (user, key) if user and key else None
        self._client = httpx.Client(auth=auth, timeout=10.0)
        atexit.register(self._client.close)

    def check_alerts(
        self, severity: str | None, service: str | None
    ) -> list[Alert]:
        params: dict[str, Any] = {
            "active": "true",
            "silenced": "false",
            "inhibited": "false",
        }
        filters: list[str] = []
        if severity is not None:
            # Defense-in-depth: main.py validates, but a future PagerDuty
            # adapter or a direct unit-test call must not be trusted.
            if severity not in _FILTERABLE_SEVERITIES:
                raise ValueError(
                    f"severity must be one of {_FILTERABLE_SEVERITIES}, got "
                    f"{severity!r}."
                )
            filters.append(f'severity="{severity}"')
        if service is not None:
            value = resolve_service(self._service_map, service)
            # Defense-in-depth: mapped values are validated at load time, so
            # this mostly guards the no-map pass-through case.
            if not _LABEL_VALUE_RE.fullmatch(value):
                raise ValueError(
                    f"Invalid Alertmanager label value {value!r}: must match "
                    f"{_LABEL_VALUE_RE.pattern}."
                )
            filters.append(f'{self._label}="{value}"')
        if filters:
            # httpx serializes a list as repeated `?filter=…&filter=…`,
            # which is the v2 contract.
            params["filter"] = filters
        try:
            resp = self._client.get(
                f"{self._url}/api/v2/alerts", params=params
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(_friendly_alertmanager_error(e)) from e
        return _filter_and_map(resp.json())


def _filter_and_map(payload: list) -> list[Alert]:
    out: list[Alert] = []
    for raw in payload or []:
        status = raw.get("status") or {}
        if status.get("state") != "active":
            continue
        # Drop silenced / inhibited entries. `silencedBy` may be `[]`, `null`,
        # or omitted on different Alertmanager versions — all three evaluate
        # falsy and pass through; only a non-empty list / truthy value drops.
        if status.get("silencedBy"):
            continue
        if status.get("inhibitedBy"):
            continue
        out.append(_to_alert(raw))
    out.sort(key=lambda a: a.started_at, reverse=True)
    return out


def _to_alert(raw: dict) -> Alert:
    labels = dict(raw.get("labels") or {})
    name = labels.get("alertname", "")
    severity = _normalize_severity(labels.get("severity"))
    # Alertmanager v2 always emits `startsAt`; hard-fail on missing rather
    # than soften to a `.get(..., now)` that hides a malformed payload.
    started_at = datetime.fromisoformat(raw["startsAt"])
    summary = (raw.get("annotations") or {}).get("summary")
    url = raw.get("generatorURL") or None
    return Alert(
        fingerprint=raw.get("fingerprint", ""),
        name=name,
        severity=severity,
        started_at=started_at,
        summary=summary,
        labels=labels,
        url=url,
    )


def _friendly_alertmanager_error(err: httpx.HTTPStatusError) -> str:
    code = err.response.status_code
    if code == 401:
        return (
            "Alertmanager returned 401 — check ALERTMANAGER_USER_ID and "
            "ALERTMANAGER_API_KEY."
        )
    if code == 400:
        return f"Alertmanager rejected the query (400): {err.response.text[:200]}"
    if code == 429:
        return "Alertmanager rate limit exceeded (429). Wait and retry."
    if code in (502, 503, 504):
        return f"Alertmanager upstream error {code}. Try again shortly."
    return f"Alertmanager API error {code}: {err.response.text[:200]}"
