# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

v0.1 ships `get_recent_deploys` with a GitHub Actions backend; `tail_logs` and `check_alerts` are not yet implemented. Source lives in `main.py`, `backends.py`, `github_actions.py`, and `models.py`, with tests under `tests/` and packaging via `pyproject.toml` / `uv.lock`. The README still documents the full three-tool design surface — treat it as the spec, not a description of what's currently wired up.

## What this is

`fieldnotes` is a minimal **MCP (Model Context Protocol) server** for on-call engineers. It exposes three read-only tools that answer the first three questions during an incident: what shipped, what are services saying, what alerts are firing.

## Architectural constraints (load-bearing)

These come from the README's "Non-goals" section and should shape every implementation decision:

- **Read-only.** No mutations of any kind — no restarts, rollbacks, scaling, alert silencing. If a proposed feature would write to a backend, it doesn't belong here.
- **No metric queries.** Alerts are in scope; raw PromQL / arbitrary metric queries are not. The boundary is: "is this firing?" yes, "what's the p99 over 24h?" no.
- **Pluggable backends, BYO credentials.** The server does not proxy auth or manage credentials — it reads from whatever backend the operator configures. Design tool implementations behind a backend interface so the deploy/log/alert source can be swapped (e.g., GitHub Actions vs. ArgoCD, Loki vs. CloudWatch, Alertmanager vs. PagerDuty).
- **Single-tenant.** One server, one operator. Don't add multi-tenancy, request-scoped auth, or per-user config.
- **~300 lines.** The README sets a deliberate size budget. Resist abstractions, frameworks, and config layers that bloat past that — the value is in the constrained surface area.

## Tool contracts

The three MCP tools and their signatures are fixed by the README:

- `get_recent_deploys(service, limit=10)` → deploy ID, status, who, when, commit SHA
- `tail_logs(service, lines=100, since="15m")` → recent log lines within a time window
- `check_alerts(severity=None, service=None)` → currently firing alerts, optionally filtered

When implementing, preserve these names, parameters, and defaults unless the user explicitly asks to change them.
