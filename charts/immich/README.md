# Immich

Umbrella chart around the [official Immich chart](https://github.com/immich-app/immich-charts),
with optional CloudNativePG, Barman Cloud backups, OT-CONTAINER-KIT Redis, a library PVC and an
HTTPRoute. Operators, Gateway and StorageClasses are prerequisites; see
[platform setup](../../docs/operators.md). No cluster-wide resources or credentials are created.

Verified upstream versions on 2026-09-29: chart **0.13.2**, server and machine-learning **v3.2.4**.
Official images: `ghcr.io/immich-app/immich-server` and `ghcr.io/immich-app/immich-machine-learning`.
The chart is distributed through OCI; its old HTTP repository is no longer updated. Container
tags are pinned independently of the upstream chart.

## Install and ownership

```sh
helm dependency build charts/immich
helm lint charts/immich --strict -f /path/to/private-values.yaml
helm template immich charts/immich -n immich -f /path/to/private-values.yaml
# After provisioning dependencies, secrets and reviewing values:
helm upgrade --install immich charts/immich -n immich --create-namespace \
  -f /path/to/private-values.yaml
```

Defaults render without operator CRDs but require an existing `immich-library` PVC,
`immich-db-rw` PostgreSQL service, `immich-db-app` Secret (username/password), `immich-redis`
service and `immich-redis-auth` Secret (password). ML creates a 10 GiB cache PVC using the
default StorageClass. Without a config file, system settings remain editable in the UI (with
CloudNativePG set `uiManagedSettings: true`, see below). Enable `library.create`, `cnpg.enabled` and `redisOperator.enabled` to
manage those services in this release. [CI values](ci/test-values.yaml) show a fictional setup.
Operator resources and the library claim have fixed configurable names; use one release per
namespace or change these names and matching server env references together.

Infrastructure values, hostnames, IPs and all secrets belong in a separate deployment repository.
This chart references existing Secrets and rejects inline OAuth client secrets. CNPG generates
its application Secret. Redis's password Secret must already exist; distribute it through your
secret manager or SOPS deployment workflow. Plain Helm values also end up in Helm release
history, even when configurationKind is Secret.

## Storage and database

- **Media:** Immich v3.2.4 needs a filesystem at `/data`; there is no native S3 primary media
  backend. Use an existing PVC or create one through NFS CSI. Keep `IMMICH_MEDIA_LOCATION`
  unchanged. The library claim has a Helm keep annotation; the StorageClass must also retain
  its PV and NFS directory. NFS PVC capacity is not a NAS quota.
- **PostgreSQL:** use block storage. The pinned
  `ghcr.io/tensorchord/cloudnative-vectorchord:17.11-1.1.1` includes the required extensions.
  Bootstrap loads `vchord.so` and creates `vchord` + `earthdistance` in the application database,
  with their dependencies (`vector` + `cube`). The app gets an ordinary database owner, not
  superuser credentials. A standard PostgreSQL image alone is insufficient.
- **Database backups:** Barman `ObjectStore` and `ScheduledBackup` archive base backups and WAL
  to an existing S3 bucket. For Garage, use the internal endpoint and generated key Secret
  (`access-key-id`/`secret-access-key`) **and set `cnpg.backup.regionKey: region`**: Garage
  checks the region in the request signature. Existing archives survive without it (the client
  retries with the region from the error body), but the first check of a new archive is a
  `HEAD` request without a body and fails with `Bad request when accessing bucket`. Bucket, key and cross-namespace Garage grant belong in
  the deployment repository, following the existing platform lifecycle.
- **Immich database dumps:** with CNPG these must be disabled because the app is not a superuser.
  Three ways, and rendering CNPG without one of them is rejected:
  `immich.immich.configuration.backup.database.enabled: false` (inline config file), an external
  config Secret containing `backup.database.enabled: false`, or **`uiManagedSettings: true`** —
  no config file at all, settings stay editable in the admin UI, and you switch the dumps off
  in the admin settings. `uiManagedSettings` and a config file exclude each
  other. The UI can export the settings as JSON if you later want to freeze them into a file.
  Database backups then require enabling Barman. Barman does not include photos/videos: configure separate
  NAS backups and test a combined media/database restore before importing originals.
- **Redis:** authenticated dedicated instance, 1 GiB PVC, AOF (`everysec`) and `noeviction`.
  Queue persistence handles normal restarts; it is not the media/database backup. The image
  runs as uid 1000, so the chart sets `podSecurityContext` (`fsGroup: 1000`); without it Redis
  cannot write to a fresh block volume and crash-loops with `Permission denied`.
- **IPv4-only nodes:** the machine-learning service binds to `[::]` and fails with `Errno 97`
  where IPv6 is unavailable. Set `IMMICH_HOST: "0.0.0.0"` in its `env` (example in
  `values.yaml`).
- **ML:** initially CPU execution, persistent 10 GiB model cache and one replica. Server and ML
  use `Recreate`; server startup allows migrations up to 20 minutes. Hardware acceleration
  needs separate node/device and image configuration.
- **NFS permissions:** the server runs as root and creates its folders under `/data`. An export
  that maps root to an unprivileged user works as long as that user may write (then all files
  belong to it); an export that maps root to `nobody` without write access fails with `EACCES`.
  Test with a throwaway pod before the first start.
- **Library access mode:** only the server mounts the library; `ReadWriteOnce` is enough on
  block storage without an RWX driver.

For two CNPG instances use separate nodes (`affinity.podAntiAffinityType: required`) and
`cnpg.primaryUpdateMethod: switchover`, so a rollout promotes the replica instead of restarting
the primary (a restart can take minutes while Postgres waits for open connections). A block
StorageClass with one storage replica is appropriate only when database replication provides
the additional copy. A single CNPG instance on single-replica storage has no failover copy.
Resource values are starting estimates, not measured capacity guarantees.

`postInitApplicationSQL` runs only on bootstrap. Changing the image does not upgrade existing SQL
extensions: follow Immich's `ALTER EXTENSION` and reindex instructions. Major PostgreSQL upgrades
and restores need a separate CNPG procedure. Retained volumes require explicit recovery or
adoption after uninstall; reinstalling the chart is not a restore procedure.

## Keycloak / OIDC

Use native Immich OIDC for browser and mobile login. A Gateway OAuth filter is unnecessary and
can interfere with API access. Create a confidential OpenID Connect client with Standard /
Authorization Code flow, client authentication and scopes `openid email profile`.
For `photos.example.com`, register these redirect URIs:

- `https://photos.example.com/auth/login`
- `https://photos.example.com/user-settings`
- `app.immich:///oauth-callback`

Issuer: `https://sso.example.com/realms/EXAMPLE`, reachable with a trusted certificate from the
server and clients. Optional backchannel logout URL:
`https://photos.example.com/api/oauth/backchannel-logout` (must be reachable from Keycloak).

Set `immich.immich.configuration: null` (clears any inline config),
`immich.immich.configurationKind: Secret` and
`immich.immich.existingConfiguration: immich-config`. The existing Secret in the release
namespace must contain **`immich-config.yaml`** with the following structure. This placeholder
is documentation only; inject the actual secret exclusively through secret management.

```yaml
backup:
  database:
    enabled: false
server:
  externalDomain: https://photos.example.com
oauth:
  enabled: true
  issuerUrl: https://sso.example.com/realms/EXAMPLE
  clientId: immich
  clientSecret: <injected-by-secret-manager>
  scope: openid email profile
  signingAlgorithm: RS256
  autoRegister: false
  autoLaunch: false
  buttonText: Login with Keycloak
passwordLogin:
  enabled: true
```

Keep a local admin login during onboarding/recovery. Pre-provision selected users and link their
OIDC accounts. `autoRegister: false` prevents self-registration; it is not a Keycloak group
authorization rule. For automatic provisioning, first restrict client access in Keycloak with
a client-specific authentication flow/policy, verify rejection of an unauthorized user, then
enable autoRegister. An `immich_role` claim sets the app role; it does not restrict login.
Removing Keycloak group membership does not necessarily revoke existing Immich sessions or API
keys; include account/session revocation in offboarding.

External config replaces the chart config entirely. File configuration disables system-settings
editing in the UI, even for keys absent from the file. External Secret changes do not trigger
a rollout here: restart the server after updating config. Alternatively manage OIDC through
the admin UI: set `uiManagedSettings: true`, keep `configuration` empty, enter issuer, client id
and client secret in the UI (the secret is then stored in Immich's database) and disable
database dumps there.

## Gateway and relevant parameters

`httpRoute` has the same shape as in the other charts of this repository: `parentRef` (or
`parentRefs`) plus `routes[]` with `hostnames` and verbatim `rules`. Point the rule at
`<release>-server:2283` and set `timeouts.request` (example in `values.yaml`: `3600s`) — Envoy
Gateway cuts requests after 15 s otherwise, which breaks video uploads. Envoy Gateway does not
buffer or limit request bodies unless you add such a policy yourself; do not add one for
uploads. Immich requires its own hostname, not a subpath; `/.well-known/immich` must reach the
app. Validate large uploads and WebSockets through every proxy hop; HTTPRoute settings cannot
change limits imposed by another proxy (e.g. Cloudflare's 100 MB per request on the free plan).

| Setting | Purpose / starting point |
|---|---|
| `immich.controllers.main.containers.main.image.tag` | Same pinned release for server and ML |
| Server `TZ` | Local timezone, e.g. Europe/Berlin; affects EXIF fallback |
| Server `DB_HOSTNAME`, `DB_PORT`, `DB_DATABASE_NAME` | Dedicated CNPG primary, 5432, immich |
| Server `DB_USERNAME`, `DB_PASSWORD` | Secret references to CNPG app credentials |
| Server `DB_VECTOR_EXTENSION` | vectorchord; leave migrations enabled |
| Server `DB_URL` | Optional Secret reference; overrides individual connection settings |
| Server `REDIS_HOSTNAME`, `REDIS_PORT`, `REDIS_PASSWORD` | Dedicated queue, 6379, Secret reference |
| Server `IMMICH_TRUSTED_PROXIES` | Pod and node ranges of your gateway; without it Immich logs the proxy as the client |
| `uiManagedSettings` | `true`: no config file, settings and OIDC in the admin UI |
| `cnpg.primaryUpdateMethod` | `switchover` with two instances |
| Server `IMMICH_ALLOW_SETUP` | Default for first-admin setup; false after onboarding |
| Server `IMMICH_LOG_LEVEL` | Default log; debug only when needed |
| `IMMICH_CONFIG_FILE` | Set and mounted by upstream for the config Secret |
| OAuth `autoRegister`, `autoLaunch`, `passwordLogin.enabled` | Initially false/false/true |
| `immich.immich.metrics.enabled` | Optional ServiceMonitors; requires Prometheus CRDs |
| ML `MACHINE_LEARNING_WORKERS` | Default 1; each worker duplicates model memory |

Docker Compose variables `UPLOAD_LOCATION` and `DB_DATA_LOCATION` do not configure containers;
Kubernetes mounts replace them. OAuth uses system configuration, not `OAUTH_*` env variables.
Credentials are server-only; ML needs no DB/Redis credentials.

## Validation and references

Run `python3 charts/immich/tests/test-chart.py` after building dependencies. Render tests cover
route wiring, persistence, bootstrap, backup references, server-only credentials, external
config mounting, UI-managed settings and invalid combinations. They do not prove operator reconciliation, NFS
permissions, migrations or OIDC login; those require a staging rollout.

- [Kubernetes](https://docs.immich.app/install/kubernetes/), [chart 0.13.2](https://github.com/immich-app/immich-charts/tree/immich-0.13.2), [app v3.2.4](https://github.com/immich-app/immich/releases/tag/v3.2.4)
- [Environment variables](https://docs.immich.app/install/environment-variables/), [config file](https://docs.immich.app/install/config-file/), [OIDC](https://docs.immich.app/administration/oauth/)
- [PostgreSQL](https://docs.immich.app/administration/postgres-standalone/), [CNPG images](https://github.com/tensorchord/cloudnative-vectorchord)
- [Reverse proxy](https://docs.immich.app/administration/reverse-proxy/), [primary S3 storage discussion](https://github.com/immich-app/immich/discussions/1683)
