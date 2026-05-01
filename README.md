# fieldnotes

A minimal, single-tenant **MCP (Model Context Protocol) server** for on-call
engineers. Read-only by design: it answers the first three questions during an
incident — what shipped, what are services saying, what alerts are firing —
without giving the model any way to mutate state.

## Tools

- `get_recent_deploys(service, limit=10)` — recent deploys for a service,
  newest first.
- `tail_logs(service, lines=100, since="15m")` — recent log lines. *(Not yet
  implemented.)*
- `check_alerts(severity=None, service=None)` — currently firing alerts.
  *(Not yet implemented.)*

## Configuration

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | GitHub PAT with `actions:read`. Required by the GitHub backend. |
| `GITHUB_REPO` | `owner/repo` form. Required by the GitHub backend. |
| `FIELDNOTES_DEPLOY_BACKEND` | Backend name. Defaults to `github`. |
| `FIELDNOTES_SERVICE_MAP` | Inline `logical=stem,…` map (see below). |
| `FIELDNOTES_SERVICE_MAP_FILE` | Path to a `.json` file containing the map. |

`FIELDNOTES_SERVICE_MAP` and `FIELDNOTES_SERVICE_MAP_FILE` are mutually
exclusive — setting both raises at startup.

### Configuring services

The GitHub Actions backend resolves `service` to a workflow filename: a call
with `service="api"` reads `.github/workflows/api.yml`. In real repos those
files are usually named `deploy-api.yml` or `release.yml`, which is exactly
the kind of tribal knowledge an on-call shouldn't have to remember at 3am.

Configure a map once so callers can use logical service names:

```bash
export FIELDNOTES_SERVICE_MAP="payments=deploy-payments,api=release,web=deploy-web"
```

Or, for orgs with many services, point at a JSON file:

```bash
export FIELDNOTES_SERVICE_MAP_FILE=/etc/fieldnotes/services.json
```

```json
{
  "payments": "deploy-payments",
  "api": "release",
  "web": "deploy-web"
}
```

**All-or-none:** once the map is set, every `service` argument must be a key.
Add all your services or leave the map unset — partial maps will reject
service names that previously passed through.

**Map keys:** free-form labels (any printable string, including spaces).
Only the resolved workflow stem must match `[A-Za-z0-9_.-]+`.

**Case-sensitive:** `payments` ≠ `Payments`.

**Pass-through default:** with both env vars unset, `service` is forwarded
verbatim.

**Big-org tip:** generate this file from your service catalog (Backstage, an
internal registry, etc.). fieldnotes is a leaf consumer, not the catalog.

## Run

```bash
uv run python main.py
```

## License

See [LICENSE](LICENSE).
