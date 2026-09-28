# helm-charts

Wrapper charts for self-hosted apps on Kubernetes: the app's upstream chart plus what is missing
when you run it yourself — database and cache via operators instead of embedded subcharts, backups
to S3, an `HTTPRoute` for the Gateway API, and optionally data from an NFS export.

| Chart | Directory | App | Requires | Optional |
|---|---|---|---|---|
| `jellyfin-helm-chart` | [`charts/jellyfin`](charts/jellyfin) | [Jellyfin](https://jellyfin.org) 12.1, upstream [jellyfin-helm](https://github.com/jellyfin/jellyfin-helm) | — | NFS, Gateway API |
| `home-assistant-helm-chart` | [`charts/home-assistant`](charts/home-assistant) | [Home Assistant](https://www.home-assistant.io), upstream [pajikos](https://github.com/pajikos/home-assistant-helm-chart) | — | CloudNativePG (recorder DB) + Barman plugin, NFS, Gateway API |
| `paperless-ngx` | [`charts/paperless-ngx`](charts/paperless-ngx) | [paperless-ngx](https://docs.paperless-ngx.com) 3.2 with Tika and Gotenberg | CloudNativePG, redis-operator | Barman plugin, Gateway API |

Why the charts build on operators instead of bundled subcharts, and how to install them (order,
tested versions, pitfalls with Garage, Keycloak and Longhorn): [`docs/operators.md`](docs/operators.md).

## Install

The charts are published as OCI artifacts in the GitHub Container Registry:

```bash
helm install jellyfin oci://ghcr.io/chilichonka/charts/jellyfin-helm-chart --version 1.1.0 \
  -n jellyfin --create-namespace -f my-values.yaml
```

The defaults are neutral and environment-agnostic: no NFS, no route, no database. Everything you
can switch on is documented in each chart's `values.yaml`, with examples. Complete made-up
examples: `charts/*/ci/test-values.yaml`.

**Secrets never go into values.** The charts only reference existing Secrets
(`credentialsSecret`, `existingSecret` …) or use the ones the operators create (CloudNativePG
creates `<cluster>-app` with the credentials).

## Examples

**Jellyfin with media from NFS**, exposed through a Gateway:

```yaml
jellyfin:
  nfs:
    enabled: true
    server: 192.0.2.10
    media: {enabled: true, path: /export/jellyfin/media}
    config: {enabled: true, path: /export/jellyfin/config}
  persistence:
    config: {enabled: true, existingClaim: jellyfin-config}
    media: {enabled: true, existingClaim: jellyfin-media}
httpRoute:
  enabled: true
  parentRef: {name: my-gateway, namespace: gateway-system, sectionName: https}
  routes:
    - name: jellyfin
      hostnames: [jellyfin.example.com]
      rules:
        - backendRefs: [{name: jellyfin, port: 8096}]
          matches: [{path: {type: PathPrefix, value: /}}]
          timeouts: {request: 10h0m0s}     # streams
```

**Home Assistant with the recorder database in CloudNativePG:**

```yaml
cnpg:
  enabled: true
  image: ghcr.io/cloudnative-pg/postgresql:17.11-standard-trixie
home-assistant:
  env:
    - name: TZ
      value: Europe/Vienna
    - name: HA_RECORDER_DB_URL
      valueFrom: {secretKeyRef: {name: home-assistant-db-app, key: uri}}
```

plus `recorder: db_url: !env_var HA_RECORDER_DB_URL` in `configuration.yaml`.

## Things worth knowing

- **jellyfin** runs with `Recreate` and exactly one replica: two instances on the same database
  corrupt it. No CPU limit — software transcoding would be throttled otherwise.
- **home-assistant** needs `trusted_proxies` behind a gateway; the defaults cover the private
  ranges. `ingress.external: true` stays set although no Ingress runs — only then does the
  subchart configure the proxy settings on a fresh install.
- **paperless-ngx:** all app settings (`paperless-ngx.paperlessVars`) are described in the
  [official documentation](https://docs.paperless-ngx.com/configuration/).
- **paperless-ngx 3** requires `PAPERLESS_SECRET_KEY` (the chart generates it once and keeps it);
  settings removed in version 3 make the render fail instead of being silently ignored.

## Development

```bash
task check        # or ./scripts/check.sh — lint, render with defaults and CI values, tests
```

Releasing a chart: bump the version in `Chart.yaml`, push a tag `<directory>-v<version>`
(e.g. `jellyfin-v1.1.0`); CI checks that tag and `Chart.yaml` agree and pushes the package to
`oci://ghcr.io/chilichonka/charts`.

## License

[Apache-2.0](LICENSE). The included upstream charts keep their own licenses.
