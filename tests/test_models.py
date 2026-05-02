from datetime import timedelta

import pytest

from models import parse_since


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("15m", timedelta(minutes=15)),
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("2d", timedelta(days=2)),
        ("30s", timedelta(seconds=30)),
        ("1s", timedelta(seconds=1)),
    ],
)
def test_parse_since_happy(raw, expected):
    assert parse_since(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "15",
        "m",
        "15min",
        "15 m",
        " 15m",
        "15m ",
        "1.5h",
        "-15m",
        "+15m",
        "15w",
        "15M",
    ],
)
def test_parse_since_malformed(raw):
    with pytest.raises(ValueError, match="since must look like"):
        parse_since(raw)


@pytest.mark.parametrize("raw", ["0s", "0m", "0h", "0d"])
def test_parse_since_zero_rejected(raw):
    """A zero window would query an empty time range. Reject early so the
    operator gets a clear error rather than an empty result."""
    with pytest.raises(ValueError, match="must be positive"):
        parse_since(raw)
