# fieldnotes

A minimal, single-tenant **MCP (Model Context Protocol) server** for on-call
engineers. Read-only by design: it answers the first three questions during an
incident — what shipped, what are services saying, what alerts are firing —
without giving the model any way to mutate state.

## Tools

- `get_recent_deploys(service, limit=10)` — recent deploys for a service,
  newest first.
- `tail_logs(service, lines=100, since="15m")` — recent log lines for a
  service, newest first. `since` accepts `15m`, `1h`, `24h`, `2d`.
- `check_alerts(severity=None, service=None)` — currently firing alerts.

## Configuration

### Deploys (GitHub Actions backend)

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | GitHub PAT with `actions:read`. Required. |
| `GITHUB_REPO` | `owner/repo` form. Required. |
| `FIELDNOTES_DEPLOY_BACKEND` | Backend name. Defaults to `github`. |
| `FIELDNOTES_SERVICE_MAP` | Inline `logical=stem,…` map (see below). |
| `FIELDNOTES_SERVICE_MAP_FILE` | Path to a `.json` file containing the map. |

### Logs (Grafana Cloud Loki backend)

| Variable | Purpose |
|---|---|
| `LOKI_URL` | Loki base URL, e.g. `https://logs-prod-006.grafana.net`. Required. |
| `LOKI_USER_ID` | Grafana Cloud user/instance ID for HTTP Basic auth. Required. |
| `LOKI_API_KEY` | Grafana Cloud access policy token. Required. |
| `FIELDNOTES_LOG_BACKEND` | Backend name. Defaults to `loki`. |
| `FIELDNOTES_LOKI_LABEL` | Loki label name to filter on. Defaults to `service`. |
| `FIELDNOTES_LOG_SERVICE_MAP` | Inline `logical=label-value,…` map (see below). |
| `FIELDNOTES_LOG_SERVICE_MAP_FILE` | Path to a `.json` file containing the map. |

Self-hosted Loki (with `X-Scope-OrgID` multi-tenancy) is not supported in
this version — only Grafana Cloud's Basic-auth flow.

The deploy, log, and alert service maps are independent: a `service` arg passes
through whichever map is relevant to the tool being called. Each pair
(`*_MAP`, `*_MAP_FILE`) is mutually exclusive — setting both raises at
startup.

### Alerts (Alertmanager backend)

| Variable | Purpose |
|---|---|
| `ALERTMANAGER_URL` | Alertmanager base URL, e.g. `https://alertmanager.internal` or `https://<stack>.grafana.net/api/alertmanager/grafana`. Required. |
| `ALERTMANAGER_USER_ID` | HTTP Basic user/instance ID. Optional; required only if `ALERTMANAGER_API_KEY` is set. |
| `ALERTMANAGER_API_KEY` | HTTP Basic password / access token. Optional; required only if `ALERTMANAGER_USER_ID` is set. |
| `FIELDNOTES_ALERT_BACKEND` | Backend name. Defaults to `alertmanager`. |
| `FIELDNOTES_ALERTMANAGER_LABEL` | Alertmanager label name to filter on. Defaults to `service`. |
| `FIELDNOTES_ALERT_SERVICE_MAP` | Inline `logical=label-value,…` map (see below). |
| `FIELDNOTES_ALERT_SERVICE_MAP_FILE` | Path to a `.json` file containing the map. |

Only currently-firing alerts are returned — silenced and inhibited alerts
are excluded.

`severity` accepts `critical`, `error`, `warning`, or `info` (case-sensitive).
Upstream synonyms (`crit`, `page`, `high`, `warn`, `medium`, `notice`, `low`,
etc.) are normalized to one of those four when surfacing alerts. Unmappable
upstream values appear as `unknown` in the response but cannot be passed as
a filter value.

**Grafana Cloud:** point `ALERTMANAGER_URL` at
`https://<stack>.grafana.net/api/alertmanager/grafana` and set
`ALERTMANAGER_USER_ID` / `ALERTMANAGER_API_KEY` to the same Basic-auth pair
used for Loki.

**Auth divergence from Loki:** unlike Loki (where `LOKI_USER_ID` /
`LOKI_API_KEY` are required), Alertmanager auth is optional — leave both
unset for an in-cluster Alertmanager that runs unauthenticated. Set both
for Grafana Cloud or any reverse-proxied deployment that requires Basic
auth. Setting only one of the pair raises at startup.

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

**Log labels:** for `tail_logs`, the `labels` field of each returned line
reflects whatever your Loki ingestion pipeline emits (pod, env, region,
etc.). If you don't want a particular identifier visible to the model,
strip it at ingest — fieldnotes surfaces what Loki provides.

## Run

```bash
uv run python main.py
```

## License

See [LICENSE](LICENSE).
