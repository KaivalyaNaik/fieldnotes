from datetime import datetime, timezone

import httpx
import pytest

from loki import (
    LokiBackend,
    _flatten_streams,
    _friendly_loki_error,
    _normalize_level,
    _validate_label_value,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("info", "info"),
        ("INFO", "info"),
        ("Information", "unknown"),
        ("informational", "info"),
        ("notice", "info"),
        ("warn", "warn"),
        ("WARNING", "warn"),
        ("error", "error"),
        ("ERR", "error"),
        ("critical", "fatal"),
        ("crit", "fatal"),
        ("fatal", "fatal"),
        ("emerg", "fatal"),
        ("alert", "fatal"),
        ("debug", "debug"),
        ("trace", "debug"),
        ("", "unknown"),
        (None, "unknown"),
        ("garbage", "unknown"),
        ("  WARN  ", "warn"),
    ],
)
def test_normalize_level(raw, expected):
    assert _normalize_level(raw) == expected


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
    with pytest.raises(ValueError, match="Loki label value"):
        _validate_label_value(value)


def _row(ns: int, msg: str) -> list[str]:
    return [str(ns), msg]


def test_flatten_streams_sorts_newest_first_and_caps():
    payload = {
        "data": {
            "result": [
                {
                    "stream": {"service": "api", "level": "info", "pod": "api-1"},
                    "values": [
                        _row(1_700_000_001_000_000_000, "first"),
                        _row(1_700_000_003_000_000_000, "third"),
                    ],
                },
                {
                    "stream": {"service": "api", "level": "ERROR", "pod": "api-2"},
                    "values": [
                        _row(1_700_000_002_000_000_000, "second"),
                        _row(1_700_000_004_000_000_000, "fourth"),
                    ],
                },
            ]
        }
    }
    out = _flatten_streams(payload, lines=3)
    assert [r.message for r in out] == ["fourth", "third", "second"]
    # Cap honored.
    assert len(out) == 3
    # Level normalization applied per-stream.
    assert out[0].level == "error"
    assert out[1].level == "info"
    # Labels surfaced raw.
    assert out[0].labels == {"service": "api", "level": "ERROR", "pod": "api-2"}


def test_flatten_streams_empty():
    assert _flatten_streams({"data": {"result": []}}, lines=10) == []
    assert _flatten_streams({}, lines=10) == []


def test_flatten_streams_ns_to_datetime_uses_seconds_not_ns():
    """Regression: dividing ns by 1_000_000_000 must happen *before* passing
    to fromtimestamp. Treating ns as seconds blows past datetime's range."""
    payload = {
        "data": {
            "result": [
                {
                    "stream": {},
                    "values": [_row(1_700_000_000_000_000_000, "ok")],
                }
            ]
        }
    }
    out = _flatten_streams(payload, lines=10)
    assert out[0].timestamp.year == 2023
    assert out[0].timestamp.tzinfo is timezone.utc


def test_flatten_streams_uses_detected_level_when_no_level_label():
    payload = {
        "data": {
            "result": [
                {
                    "stream": {"detected_level": "warn"},
                    "values": [_row(1_700_000_000_000_000_000, "ok")],
                }
            ]
        }
    }
    out = _flatten_streams(payload, lines=10)
    assert out[0].level == "warn"


def _http_status_error(status: int, text: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(status, text=text, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


@pytest.mark.parametrize(
    "code, fragment",
    [
        (401, "LOKI_USER_ID"),
        (400, "rejected"),
        (429, "rate limit"),
        (502, "upstream"),
        (503, "upstream"),
        (504, "upstream"),
    ],
)
def test_friendly_loki_error(code, fragment):
    msg = _friendly_loki_error(_http_status_error(code, text="parse error"))
    assert fragment in msg


def test_friendly_loki_error_400_includes_response_body():
    """400 must surface Loki's parse message — operators rely on it."""
    err = _http_status_error(400, text="parse error: unexpected token")
    msg = _friendly_loki_error(err)
    assert "parse error: unexpected token" in msg


def test_friendly_loki_error_500_truncates_long_body():
    err = _http_status_error(500, text="x" * 300)
    msg = _friendly_loki_error(err)
    assert "x" * 200 in msg
    assert "x" * 201 not in msg


@pytest.fixture
def loki_creds(monkeypatch):
    monkeypatch.setenv("LOKI_URL", "https://logs.example.test")
    monkeypatch.setenv("LOKI_USER_ID", "12345")
    monkeypatch.setenv("LOKI_API_KEY", "fake-key")
    monkeypatch.delenv("FIELDNOTES_LOG_SERVICE_MAP", raising=False)
    monkeypatch.delenv("FIELDNOTES_LOG_SERVICE_MAP_FILE", raising=False)
    monkeypatch.delenv("FIELDNOTES_LOKI_LABEL", raising=False)


def test_init_requires_loki_url(monkeypatch, loki_creds):
    monkeypatch.delenv("LOKI_URL")
    with pytest.raises(RuntimeError, match="LOKI_URL is required"):
        LokiBackend()


def test_init_requires_loki_creds(monkeypatch, loki_creds):
    monkeypatch.delenv("LOKI_USER_ID")
    with pytest.raises(RuntimeError, match="LOKI_USER_ID and LOKI_API_KEY"):
        LokiBackend()


def test_init_strips_trailing_slash_from_url(monkeypatch, loki_creds):
    monkeypatch.setenv("LOKI_URL", "https://logs.example.test/")
    backend = LokiBackend()
    assert backend._url == "https://logs.example.test"


def test_init_default_label_is_service(loki_creds):
    backend = LokiBackend()
    assert backend._label == "service"


def test_init_custom_label_accepted(monkeypatch, loki_creds):
    monkeypatch.setenv("FIELDNOTES_LOKI_LABEL", "app")
    backend = LokiBackend()
    assert backend._label == "app"


@pytest.mark.parametrize(
    "label", ['service"};drop', "ser vice", "service-name", "1service"]
)
def test_init_rejects_malformed_label(monkeypatch, loki_creds, label):
    """LogQL injection surface — label name must match the Prometheus
    label-name grammar. Misconfiguration fails fast at init."""
    monkeypatch.setenv("FIELDNOTES_LOKI_LABEL", label)
    with pytest.raises(RuntimeError, match="FIELDNOTES_LOKI_LABEL"):
        LokiBackend()


def test_init_empty_label_falls_back_to_default(monkeypatch, loki_creds):
    """Empty/whitespace env var is treated as unset — operators commonly
    leave shell vars defined but blank."""
    monkeypatch.setenv("FIELDNOTES_LOKI_LABEL", "   ")
    assert LokiBackend()._label == "service"


def test_init_wires_log_service_map(monkeypatch, loki_creds):
    monkeypatch.setenv("FIELDNOTES_LOG_SERVICE_MAP", "payments=payments-prod")
    backend = LokiBackend()
    assert backend._service_map == {"payments": "payments-prod"}


def test_init_rejects_invalid_label_value_in_map(monkeypatch, loki_creds):
    monkeypatch.setenv("FIELDNOTES_LOG_SERVICE_MAP", 'payments=foo"};drop')
    with pytest.raises(
        RuntimeError, match=r"FIELDNOTES_LOG_SERVICE_MAP.*Loki label value"
    ):
        LokiBackend()


class _FakeResp:
    def __init__(self, payload: dict | None = None):
        self._payload = payload or {"data": {"result": []}}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_tail_logs_builds_query_with_default_label(monkeypatch, loki_creds):
    backend = LokiBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.tail_logs("payments-prod", lines=10, since="15m")
    assert captured["url"] == "https://logs.example.test/loki/api/v1/query_range"
    assert captured["params"]["query"] == '{service="payments-prod"}'
    assert captured["params"]["limit"] == "10"
    assert captured["params"]["direction"] == "backward"


def test_tail_logs_uses_custom_label(monkeypatch, loki_creds):
    monkeypatch.setenv("FIELDNOTES_LOKI_LABEL", "app")
    backend = LokiBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.tail_logs("payments-prod", lines=5, since="1h")
    assert captured["params"]["query"] == '{app="payments-prod"}'


def test_tail_logs_resolves_through_map(monkeypatch, loki_creds):
    monkeypatch.setenv("FIELDNOTES_LOG_SERVICE_MAP", "payments=payments-prod")
    backend = LokiBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.tail_logs("payments", lines=5, since="1h")
    assert captured["params"]["query"] == '{service="payments-prod"}'


def test_tail_logs_strict_unknown_when_map_set(monkeypatch, loki_creds):
    monkeypatch.setenv(
        "FIELDNOTES_LOG_SERVICE_MAP", "payments=payments-prod,api=api-prod"
    )
    backend = LokiBackend()
    with pytest.raises(
        ValueError, match=r"unknown service 'paymets'\. known: api, payments"
    ):
        backend.tail_logs("paymets", lines=5, since="1h")


def test_tail_logs_passthrough_when_map_empty(monkeypatch, loki_creds):
    backend = LokiBackend()
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.tail_logs("payments-prod", lines=5, since="1h")
    assert captured["params"]["query"] == '{service="payments-prod"}'


def test_tail_logs_rejects_bad_label_value_even_if_map_loader_was_bypassed(
    monkeypatch, loki_creds
):
    """Last-line-of-defense: hand-craft a malformed _service_map (bypassing
    the loader) and confirm validation still rejects before the URL is built."""
    backend = LokiBackend()
    backend._service_map = {"x": 'evil"};drop'}
    with pytest.raises(ValueError, match="Invalid Loki label value"):
        backend.tail_logs("x", lines=5, since="1h")


def test_tail_logs_window_uses_now_minus_since(monkeypatch, loki_creds):
    """`start` must equal `end - parse_since(since)`, both as ns. Pin the
    arithmetic so a regression in unit conversion is caught."""
    backend = LokiBackend()
    fixed_now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    class _FixedDatetime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr("loki.datetime", _FixedDatetime)
    captured: dict = {}

    def fake_get(url, params=None):
        captured["params"] = params
        return _FakeResp()

    monkeypatch.setattr(backend._client, "get", fake_get)
    backend.tail_logs("payments", lines=5, since="15m")
    end_ns = int(captured["params"]["end"])
    start_ns = int(captured["params"]["start"])
    assert end_ns == int(fixed_now.timestamp() * 1_000_000_000)
    assert end_ns - start_ns == 15 * 60 * 1_000_000_000


def test_tail_logs_maps_400_to_friendly_error(monkeypatch, loki_creds):
    backend = LokiBackend()

    class _BadResp:
        def raise_for_status(self):
            req = httpx.Request("GET", "https://example.test")
            resp = httpx.Response(400, text="parse error", request=req)
            raise httpx.HTTPStatusError("400", request=req, response=resp)

        def json(self):
            return {}

    monkeypatch.setattr(backend._client, "get", lambda url, params=None: _BadResp())
    with pytest.raises(RuntimeError, match=r"Loki rejected the query"):
        backend.tail_logs("payments", lines=5, since="1h")
