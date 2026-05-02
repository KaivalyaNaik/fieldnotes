from datetime import timezone

import httpx
import pytest

from alertmanager import (
    AlertmanagerBackend,
    _filter_and_map,
    _friendly_alertmanager_error,
    _normalize_severity,
    _to_alert,
    _validate_label_value,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("critical", "critical"),
        ("CRITICAL", "critical"),
        ("crit", "critical"),
        ("page", "critical"),
        ("error", "error"),
        ("ERR", "error"),
        ("high", "error"),
        ("warning", "warning"),
        ("WARN", "warning"),
        ("medium", "warning"),
        ("info", "info"),
        ("Informational", "info"),
        ("notice", "info"),
        ("low", "info"),
        ("", "unknown"),
        (None, "unknown"),
        ("garbage", "unknown"),
        ("  CRIT  ", "critical"),
    ],
)
def test_normalize_severity(raw, expected):
    assert _normalize_severity(raw) == expected


@pytest.mark.parametrize(
    "value", ["payments", "payments-prod", "payments_v2.us-east-1", "a/b/c", "x"]
)
def test_validate_label_value_accepts(value):
    _validate_label_value(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        'foo"};drop',
        "foo bar",
        "foo{bar}",
        "foo\\bar",
        "foo|bar",
        "ünicode",
    ],
)
def test_validate_label_value_rejects(value):
    with pytest.raises(ValueError, match="Alertmanager label value"):
        _validate_label_value(value)


def _alert_row(
    fingerprint: str,
    starts_at: str,
    *,
    name: str = "HighErrorRate",
    severity: str | None = "critical",
    state: str = "active",
    silenced_by=None,
    inhibited_by=None,
    summary: str | None = "errors over threshold",
    generator_url: str | None = "https://prom.example.test/graph",
    extra_labels: dict | None = None,
) -> dict:
    labels = {"alertname": name}
    if severity is not None:
        labels["severity"] = severity
    if extra_labels:
        labels.update(extra_labels)
    annotations = {"summary": summary} if summary is not None else {}
    status: dict = {"state": state}
    # Only attach silencedBy / inhibitedBy when explicitly given so we can
    # cover both "key present" and "key absent" shapes.
    if silenced_by is not None:
        status["silencedBy"] = silenced_by
    if inhibited_by is not None:
        status["inhibitedBy"] = inhibited_by
    return {
        "fingerprint": fingerprint,
        "labels": labels,
        "annotations": annotations,
        "status": status,
        "startsAt": starts_at,
        "generatorURL": generator_url,
    }


def test_to_alert_happy_path():
    raw = _alert_row(
        "fp-1",
        "2026-05-01T12:00:00Z",
        name="HighErrorRate",
        severity="critical",
        extra_labels={"team": "payments"},
    )
    a = _to_alert(raw)
    assert a.fingerprint == "fp-1"
    assert a.name == "HighErrorRate"
    assert a.severity == "critical"
    assert a.summary == "errors over threshold"
    assert a.url == "https://prom.example.test/graph"
    assert a.labels == {
        "alertname": "HighErrorRate",
        "severity": "critical",
        "team": "payments",
    }
    assert a.started_at.year == 2026
    assert a.started_at.tzinfo is not None
    # ISO with trailing Z parses to UTC on 3.11+.
    assert a.started_at.utcoffset() == timezone.utc.utcoffset(a.started_at)


def test_to_alert_defensive_defaults():
    raw = {
        "fingerprint": "fp-2",
        "labels": {"alertname": "Mystery"},
        "startsAt": "2026-05-01T12:00:00Z",
        "status": {"state": "active"},
    }
    a = _to_alert(raw)
    assert a.fingerprint == "fp-2"
    assert a.name == "Mystery"
    assert a.severity == "unknown"
    assert a.summary is None
    assert a.url is None
    assert a.labels == {"alertname": "Mystery"}


def test_to_alert_empty_generator_url_becomes_none():
    raw = {
        "fingerprint": "fp-3",
        "labels": {"alertname": "X"},
        "startsAt": "2026-05-01T12:00:00Z",
        "status": {"state": "active"},
        "generatorURL": "",
    }
    assert _to_alert(raw).url is None


def test_filter_and_map_drops_non_firing():
    payload = [
        # Active, no silence/inhibit — kept.
        _alert_row("a", "2026-05-01T12:00:00Z"),
        # Active but silenced — dropped.
        _alert_row(
            "b", "2026-05-01T12:01:00Z", silenced_by=["silence-1"]
        ),
        # Suppressed state — dropped.
        _alert_row("c", "2026-05-01T12:02:00Z", state="suppressed"),
        # Unprocessed state — dropped.
        _alert_row("d", "2026-05-01T12:03:00Z", state="unprocessed"),
    ]
    out = _filter_and_map(payload)
    assert [a.fingerprint for a in out] == ["a"]


def test_filter_and_map_silenced_shape_variants():
    """`silencedBy` may be `[]`, `null`, or omitted on different Alertmanager
    versions. All three pass through; only a non-empty list drops."""
    rows = [
        _alert_row("empty", "2026-05-01T12:00:00Z", silenced_by=[]),
        # `silenced_by=None` → key omitted because of helper signature, so
        # build the absent-key row manually too:
        {
            "fingerprint": "absent",
            "labels": {"alertname": "X", "severity": "info"},
            "annotations": {},
            "status": {"state": "active"},
            "startsAt": "2026-05-01T12:01:00Z",
            "generatorURL": None,
        },
        {
            "fingerprint": "null",
            "labels": {"alertname": "X", "severity": "info"},
            "annotations": {},
            "status": {"state": "active", "silencedBy": None},
            "startsAt": "2026-05-01T12:02:00Z",
            "generatorURL": None,
        },
        _alert_row("dropped", "2026-05-01T12:03:00Z", silenced_by=["abc"]),
    ]
    out = _filter_and_map(rows)
    assert sorted(a.fingerprint for a in out) == ["absent", "empty", "null"]


def test_filter_and_map_inhibited_shape_variants():
    rows = [
        _alert_row("empty", "2026-05-01T12:00:00Z", inhibited_by=[]),
        {
            "fingerprint": "absent",
            "labels": {"alertname": "X", "severity": "info"},
            "annotations": {},
            "status": {"state": "active"},
            "startsAt": "2026-05-01T12:01:00Z",
            "generatorURL": None,
        },
        {
            "fingerprint": "null",
            "labels": {"alertname": "X", "severity": "info"},
            "annotations": {},
            "status": {"state": "active", "inhibitedBy": None},
            "startsAt": "2026-05-01T12:02:00Z",
            "generatorURL": None,
        },
        _alert_row("dropped", "2026-05-01T12:03:00Z", inhibited_by=["xyz"]),
    ]
    out = _filter_and_map(rows)
    assert sorted(a.fingerprint for a in out) == ["absent", "empty", "null"]


def test_filter_and_map_sorts_newest_first():
    payload = [
        _alert_row("oldest", "2026-05-01T12:00:00Z"),
        _alert_row("newest", "2026-05-01T12:05:00Z"),
        _alert_row("middle", "2026-05-01T12:02:30Z"),
    ]
    out = _filter_and_map(payload)
    assert [a.fingerprint for a in out] == ["newest", "middle", "oldest"]


def test_filter_and_map_empty_payload():
    assert _filter_and_map([]) == []


def _http_status_error(status: int, text: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(status, text=text, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


@pytest.mark.parametrize(
    "code, fragment",
    [
        (401, "ALERTMANAGER_USER_ID"),
        (400, "rejected"),
        (429, "rate limit"),
        (502, "upstream"),
        (503, "upstream"),
        (504, "upstream"),
    ],
)
def test_friendly_alertmanager_error(code, fragment):
    msg = _friendly_alertmanager_error(_http_status_error(code, text="parse error"))
    assert fragment in msg


def test_friendly_alertmanager_error_400_includes_response_body():
    err = _http_status_error(400, text="invalid matcher")
    msg = _friendly_alertmanager_error(err)
    assert "invalid matcher" in msg


def test_friendly_alertmanager_error_500_truncates_long_body():
    err = _http_status_error(500, text="x" * 300)
    msg = _friendly_alertmanager_error(err)
    assert "x" * 200 in msg
    assert "x" * 201 not in msg


def test_friendly_alertmanager_error_401_names_both_env_vars():
    msg = _friendly_alertmanager_error(_http_status_error(401))
    assert "ALERTMANAGER_USER_ID" in msg
    assert "ALERTMANAGER_API_KEY" in msg


@pytest.fixture
def am_creds(monkeypatch):
    monkeypatch.setenv("ALERTMANAGER_URL", "https://am.example.test")
    monkeypatch.setenv("ALERTMANAGER_USER_ID", "12345")
    monkeypatch.setenv("ALERTMANAGER_API_KEY", "fake-key")
    monkeypatch.delenv("FIELDNOTES_ALERT_SERVICE_MAP", raising=False)
    monkeypatch.delenv("FIELDNOTES_ALERT_SERVICE_MAP_FILE", raising=False)
    monkeypatch.delenv("FIELDNOTES_ALERTMANAGER_LABEL", raising=False)


def test_init_requires_alertmanager_url(monkeypatch, am_creds):
    monkeypatch.delenv("ALERTMANAGER_URL")
    with pytest.raises(RuntimeError, match="ALERTMANAGER_URL is required"):
        AlertmanagerBackend()


def test_init_strips_trailing_slash_from_url(monkeypatch, am_creds):
    monkeypatch.setenv("ALERTMANAGER_URL", "https://am.example.test/")
    backend = AlertmanagerBackend()
    assert backend._url == "https://am.example.test"


def test_init_default_label_is_service(am_creds):
    assert AlertmanagerBackend()._label == "service"


def test_init_custom_label_accepted(monkeypatch, am_creds):
    monkeypatch.setenv("FIELDNOTES_ALERTMANAGER_LABEL", "app")
    assert AlertmanagerBackend()._label == "app"


@pytest.mark.parametrize(
    "label", ['service"};drop', "ser vice", "service-name", "1service"]
)
def test_init_rejects_malformed_label(monkeypatch, am_creds, label):
    monkeypatch.setenv("FIELDNOTES_ALERTMANAGER_LABEL", label)
    with pytest.raises(RuntimeError, match="FIELDNOTES_ALERTMANAGER_LABEL"):
        AlertmanagerBackend()


def test_init_empty_label_falls_back_to_default(monkeypatch, am_creds):
    monkeypatch.setenv("FIELDNOTES_ALERTMANAGER_LABEL", "   ")
    assert AlertmanagerBackend()._label == "service"


def test_init_wires_alert_service_map(monkeypatch, am_creds):
    monkeypatch.setenv("FIELDNOTES_ALERT_SERVICE_MAP", "payments=payments-prod")
    backend = AlertmanagerBackend()
    assert backend._service_map == {"payments": "payments-prod"}


def test_init_rejects_invalid_label_value_in_map(monkeypatch, am_creds):
    monkeypatch.setenv("FIELDNOTES_ALERT_SERVICE_MAP", 'payments=foo"};drop')
    with pytest.raises(
        RuntimeError, match=r"FIELDNOTES_ALERT_SERVICE_MAP.*Alertmanager label value"
    ):
        AlertmanagerBackend()


def test_init_no_auth_when_both_creds_unset(monkeypatch, am_creds):
    monkeypatch.delenv("ALERTMANAGER_USER_ID")
    monkeypatch.delenv("ALERTMANAGER_API_KEY")
    backend = AlertmanagerBackend()
    assert backend._client.auth is None


def test_init_basic_auth_when_both_creds_set(am_creds):
    backend = AlertmanagerBackend()
    assert backend._client.auth is not None


def test_init_rejects_user_without_key(monkeypatch, am_creds):
    monkeypatch.delenv("ALERTMANAGER_API_KEY")
    with pytest.raises(
        RuntimeError,
        match=r"ALERTMANAGER_USER_ID and ALERTMANAGER_API_KEY",
    ):
        AlertmanagerBackend()


def test_init_rejects_key_without_user(monkeypatch, am_creds):
    monkeypatch.delenv("ALERTMANAGER_USER_ID")
    with pytest.raises(
        RuntimeError,
        match=r"ALERTMANAGER_USER_ID and ALERTMANAGER_API_KEY",
    ):
        AlertmanagerBackend()


class _FakeResp:
    def __init__(self, payload: list | None = None):
        self._payload = payload if payload is not None else []

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list:
        return self._payload


def test_check_alerts_no_filters(monkeypatch, am_creds):
    backend = AlertmanagerBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.check_alerts(severity=None, service=None)
    assert captured["url"] == "https://am.example.test/api/v2/alerts"
    assert captured["params"] == {
        "active": "true",
        "silenced": "false",
        "inhibited": "false",
    }


def test_check_alerts_severity_only(monkeypatch, am_creds):
    backend = AlertmanagerBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.check_alerts(severity="critical", service=None)
    assert captured["params"]["filter"] == ['severity="critical"']


def test_check_alerts_service_passthrough(monkeypatch, am_creds):
    backend = AlertmanagerBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.check_alerts(severity=None, service="payments")
    assert captured["params"]["filter"] == ['service="payments"']


def test_check_alerts_service_via_map(monkeypatch, am_creds):
    monkeypatch.setenv("FIELDNOTES_ALERT_SERVICE_MAP", "payments=payments-prod")
    backend = AlertmanagerBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.check_alerts(severity=None, service="payments")
    assert captured["params"]["filter"] == ['service="payments-prod"']


def test_check_alerts_both_filters(monkeypatch, am_creds):
    backend = AlertmanagerBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.check_alerts(severity="warning", service="api")
    assert captured["params"]["filter"] == [
        'severity="warning"',
        'service="api"',
    ]


def test_check_alerts_uses_custom_label(monkeypatch, am_creds):
    monkeypatch.setenv("FIELDNOTES_ALERTMANAGER_LABEL", "app")
    backend = AlertmanagerBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.check_alerts(severity=None, service="payments")
    assert captured["params"]["filter"] == ['app="payments"']


def test_check_alerts_resolves_before_regex_check(monkeypatch, am_creds):
    """A logical name with a space (regex-illegal) must reach Alertmanager
    as the resolved label value, never as the unresolved input. Pins
    resolve→validate ordering at the backend's call site."""
    monkeypatch.setenv(
        "FIELDNOTES_ALERT_SERVICE_MAP", "my service=payments-prod"
    )
    backend = AlertmanagerBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.check_alerts(severity=None, service="my service")
    assert 'service="payments-prod"' in captured["params"]["filter"]
    assert all("my service" not in f for f in captured["params"]["filter"])


def test_check_alerts_strict_unknown_when_map_set(monkeypatch, am_creds):
    monkeypatch.setenv(
        "FIELDNOTES_ALERT_SERVICE_MAP", "payments=payments-prod,api=api-prod"
    )
    backend = AlertmanagerBackend()
    with pytest.raises(
        ValueError, match=r"unknown service 'paymets'\. known: api, payments"
    ):
        backend.check_alerts(severity=None, service="paymets")


def test_check_alerts_rejects_bad_label_value_even_if_map_loader_was_bypassed(
    monkeypatch, am_creds
):
    """Last-line-of-defense: hand-craft a malformed _service_map (bypassing
    the loader) and confirm validation still rejects before the URL is built."""
    backend = AlertmanagerBackend()
    backend._service_map = {"x": 'evil"};drop'}
    with pytest.raises(ValueError, match="Invalid Alertmanager label value"):
        backend.check_alerts(severity=None, service="x")


def test_check_alerts_rejects_bogus_severity_at_backend(monkeypatch, am_creds):
    """Defense-in-depth: main.py validates, but a direct backend call must
    not accept arbitrary severity strings."""
    backend = AlertmanagerBackend()
    with pytest.raises(ValueError, match="severity must be one of"):
        backend.check_alerts(severity="bogus", service=None)


def test_check_alerts_rejects_unknown_severity_at_backend(monkeypatch, am_creds):
    """`unknown` is an output bucket only — it must not be accepted as a
    filter value, even if a future change sources the allow-list from
    `_SEVERITIES.values()`. Pin it explicitly."""
    backend = AlertmanagerBackend()
    with pytest.raises(ValueError, match="severity must be one of"):
        backend.check_alerts(severity="unknown", service=None)


def test_check_alerts_maps_400_to_friendly_error(monkeypatch, am_creds):
    backend = AlertmanagerBackend()

    class _BadResp:
        def raise_for_status(self):
            req = httpx.Request("GET", "https://example.test")
            resp = httpx.Response(400, text="bad matcher", request=req)
            raise httpx.HTTPStatusError("400", request=req, response=resp)

        def json(self):
            return []

    monkeypatch.setattr(backend._client, "get", lambda url, params=None: _BadResp())
    with pytest.raises(RuntimeError, match=r"Alertmanager rejected the query"):
        backend.check_alerts(severity=None, service=None)


def test_check_alerts_400_truncates_long_body(monkeypatch, am_creds):
    backend = AlertmanagerBackend()

    class _BadResp:
        def raise_for_status(self):
            req = httpx.Request("GET", "https://example.test")
            resp = httpx.Response(400, text="y" * 300, request=req)
            raise httpx.HTTPStatusError("400", request=req, response=resp)

        def json(self):
            return []

    monkeypatch.setattr(backend._client, "get", lambda url, params=None: _BadResp())
    with pytest.raises(RuntimeError) as exc_info:
        backend.check_alerts(severity=None, service=None)
    msg = str(exc_info.value)
    assert "y" * 200 in msg
    assert "y" * 201 not in msg
