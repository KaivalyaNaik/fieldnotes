"""Shared loader and resolver for flat `{logical_name: backend_identifier}` maps."""

import json
import os
from collections.abc import Callable
from pathlib import Path


def load_flat_map(
    env_var: str,
    file_env_var: str,
    validate: Callable[[str], None],
) -> dict[str, str]:
    """Read a flat str→str map from `env_var` (`k=v,k=v` form) or `file_env_var`
    (path to a JSON object). Each value is run through `validate`, which must
    raise `ValueError` on bad input; the loader rewraps as `RuntimeError`
    with origin context. Returns `{}` if neither var is set; raises if both.
    """
    raw_env = os.environ.get(env_var, "").strip()
    raw_file = os.environ.get(file_env_var, "").strip()
    if raw_env and raw_file:
        raise RuntimeError(f"set {env_var} or {file_env_var}, not both")
    if raw_env:
        return _parse_env(env_var, raw_env, validate)
    if raw_file:
        return _load_file(file_env_var, raw_file, validate)
    return {}


def resolve_service(mapping: dict[str, str], service: str) -> str:
    """Pass-through if `mapping` is empty; lookup if `service` is a key;
    raise `ValueError` listing known keys otherwise. Codifies the README's
    all-or-none rule: once a map is set, every caller must use a key.
    """
    if not mapping:
        return service
    if service in mapping:
        return mapping[service]
    raise ValueError(
        f"unknown service {service!r}. known: {', '.join(sorted(mapping))}"
    )


def _parse_env(
    env_var: str, raw: str, validate: Callable[[str], None]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise RuntimeError(f"{env_var} entry {pair!r} missing '='")
        key, _, val = pair.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            raise RuntimeError(f"{env_var} entry {pair!r}: empty key")
        if not val:
            raise RuntimeError(f"{env_var} entry {pair!r}: empty value")
        try:
            validate(val)
        except ValueError as e:
            raise RuntimeError(f"{env_var} entry {pair!r}: {e}") from None
        if key in out:
            raise RuntimeError(f"{env_var} duplicate key {key!r}")
        out[key] = val
    return out


def _load_file(
    file_env_var: str, path_str: str, validate: Callable[[str], None]
) -> dict[str, str]:
    path = Path(path_str)
    prefix = f"{file_env_var}={path_str}"
    if path.suffix != ".json":
        raise RuntimeError(f"{prefix}: only .json files are supported")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"{prefix}: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{prefix}: invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"{prefix}: must contain a flat object of string→string")
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str) or not k or not v:
            raise RuntimeError(
                f"{prefix}: must contain a flat object of string→string"
            )
        try:
            validate(v)
        except ValueError as e:
            raise RuntimeError(f"{prefix}: {e} (for {k!r})") from None
    return data
