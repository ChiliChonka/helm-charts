# n8n

Umbrella chart around the [official n8n chart](https://github.com/n8n-io/n8n-hosting/tree/main/charts/n8n)
(`oci://ghcr.io/n8n-io/n8n-helm-chart/n8n`), with optional CloudNativePG, Barman Cloud backups,
OT-CONTAINER-KIT Redis, an HTTPRoute and NetworkPolicies. Operators, Gateway and StorageClasses are
prerequisites; see [platform setup](../../docs/operators.md). No cluster-wide resources or
credentials are created.

Verified upstream versions on 2026-11-15: chart **1.19.0**, n8n **2.47.4**
(`docker.n8n.io/n8nio/n8n`, task runner `n8nio/runners`). The upstream 1.x line follows n8n 2.x;
chart 2.0 ships with n8n 3.0 and changes values keys (its `UPGRADING.md`). `appVersion` here
must equal the upstream chart's (checked by the tests): the image tag follows the upstream chart.

## What it sets up

- **Queue mode:** main (editor, webhooks) and one worker. Binary data goes to the database
  (n8n's default in queue mode). S3 for binary data needs an n8n Business/Enterprise licence;
  without one n8n does not start.
- **Task runners** as a sidecar on the worker (external mode), the setup n8n recommends and the
  default from upstream chart 2.0 on.
- **Optional webhook pods** (`n8n.webhookProcessor.enabled`, off by default, one replica when on):
  they take the production webhook calls, so a burst of calls does not slow down the editor. Main
  then no longer serves production webhooks: the route must send the five webhook paths to them
  (see Route). The render fails if one is missing.
- **Hardening over upstream defaults:** no Role that lets the pods read the namespace's Secrets,
  no ServiceAccount token in the pods (a Code node could otherwise read the database password),
  and an own NetworkPolicy instead of upstream's (which allows ingress from anywhere and blocks
  egress except DNS, 443, Postgres and Redis, so workflows calling e.g. Home Assistant fail).

## Install

```sh
helm dependency build charts/n8n
helm template n8n charts/n8n -n automation -f /path/to/private-values.yaml
helm upgrade --install n8n charts/n8n -n automation --create-namespace -f /path/to/private-values.yaml
```

Create these Secrets first (SOPS or your secret manager, never values):

| Secret | Keys | Note |
|---|---|---|
| `n8n-core` | `N8N_ENCRYPTION_KEY`, `N8N_RUNNERS_AUTH_TOKEN`, `N8N_HOST`, `N8N_PORT` (`5678`), `N8N_PROTOCOL` (`https`) | The upstream chart reads host, port and protocol from the same Secret. **Keep the encryption key**: n8n encrypts stored credentials with it. |
| `n8n-redis-auth` | `password` | Redis password, read by the operator and by n8n. |
| backup credentials | access key, secret key, region | Only with `cnpg.backup.enabled`. |

CloudNativePG creates `n8n-db-app` itself. Set `n8n.editor.url` and `n8n.webhook.url` to the
route's public URL: without an Ingress the upstream chart cannot derive them, and n8n builds
webhook and OAuth callback URLs from them. `templates/validate.yaml` fails the render when database,
Redis or URLs do not fit together.

Behind a gateway, set `N8N_PROXY_HOPS: "1"` under `n8n.config.extraEnv` so n8n trusts one
`X-Forwarded-For` hop (rate limiting, client IPs in the log).

**Nodes without IPv6:** n8n listens on `::` by default and then exits with
`n8n's address '::' is not available`. Set both addresses under `n8n.config.extraEnv` (reaches
main, worker and webhook pods):

```yaml
- {name: N8N_LISTEN_ADDRESS, value: "0.0.0.0"}
- {name: N8N_WORKER_SERVER_ADDRESS, value: "0.0.0.0"}   # worker health endpoint
```

**Task runner idle shutdown:** the runner sidecar stops after 15 s without tasks
(`n8n.taskRunners.launcher.autoShutdownTimeout`), and the next Code-node execution waits for it
to start again: measured 13 s instead of 0.4 s per webhook call after a pause. `0` keeps it
running.

[CI values](ci/test-values.yaml) show a complete fictional setup.

## Route

The upstream Services are `<release>-n8n-main` and `<release>-n8n-webhook-processor` (just
`<release>-main` and `<release>-webhook-processor` when the release name contains `n8n`, e.g.
`n8n-main`), port 5678. Without webhook pods
one rule for `/` to main is enough. With webhook pods, add a rule like the upstream webhook
Ingress:

```yaml
- backendRefs: [{name: n8n-webhook-processor, port: 5678}]
  matches:
    - {path: {type: PathPrefix, value: /webhook/}}
    - {path: {type: PathPrefix, value: /webhook-waiting/}}
    - {path: {type: PathPrefix, value: /form/}}
    - {path: {type: PathPrefix, value: /form-waiting/}}
    - {path: {type: PathPrefix, value: /mcp/}}
  timeouts: {request: 3600s}
```

Test paths (`/webhook-test/`, `/form-test/`, `/mcp-test/`) stay on main via `/`. Envoy Gateway cuts
requests after 15 s unless `timeouts.request` is set; a webhook that waits for its workflow
("Respond to Webhook") needs a long value.

## Tests

`python3 charts/n8n/tests/test-chart.py` (part of `scripts/check.sh`): wiring of database, Redis,
Secrets and route against the rendered upstream chart, stable render (no generated tokens),
hardening, NetworkPolicies, and that mismatched values fail.
