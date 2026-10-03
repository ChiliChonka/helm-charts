# ownCloud Infinite Scale (oCIS)

Own chart for [oCIS](https://github.com/owncloud/ocis) **8.2.1** as a single binary
(`ocis server`): one pod runs all services with the embedded NATS, user directory (IDM) and
search index. Persistent volumes, optional Keycloak/OIDC login and an HTTPRoute for the Gateway
API. No cluster-wide resources and no credentials are created.

**Why not a wrapper around the upstream chart:** [owncloud/ocis-charts](https://github.com/owncloud/ocis-charts)
describes itself as experimental; its last release is 0.5.0 (2023), newer versions exist only in
Git. It deploys every service separately (about 30 Deployments) and needs an external NATS and
ReadWriteMany storage. For a single installation, ownCloud's own Docker examples run the single
binary; this chart does the same on Kubernetes.

**No database, no cache:** oCIS needs neither PostgreSQL nor Redis. Everything lives on the
volumes, so there is no CloudNativePG or redis-operator resource here.

## Install

```sh
helm template ocis charts/ocis -n ocis -f /path/to/private-values.yaml
helm upgrade --install ocis charts/ocis -n ocis --create-namespace -f /path/to/private-values.yaml
```

Required values: `url`, plus either `oidc.*` or `admin.existingSecret` (the minimum is in
[ci/required-values.yaml](ci/required-values.yaml), a full fictional setup in
[ci/test-values.yaml](ci/test-values.yaml)). The render fails without them.

## How it starts

- An init container runs `ocis init` **once** and writes `/etc/ocis/ocis.yaml` with random
  secrets (JWT, transfer secret, service passwords …). Its output contains the admin password and
  is discarded. On every later start it finds the file and leaves it alone.
- The admin password comes from `admin.existingSecret` and is only read when the user directory
  creates the admin on the first start. Changing the Secret later does not change the password.
- `Recreate` and exactly one replica: bolt databases, NATS JetStream and the search index on the
  data volume do not tolerate a second pod.

## Volumes and backup

| Volume | Path | Content | Storage |
|---|---|---|---|
| `config` | `/etc/ocis` | `ocis.yaml` with all internal secrets | block, 1 GiB |
| `data` | `/var/lib/ocis` | user directory, shares and settings, NATS, search index, thumbnails; the spaces too unless `files` is on | block |
| `files` (optional) | `/var/lib/ocis-files` | the user spaces: content and their metadata, `STORAGE_USERS_OCIS_ROOT` | e.g. NFS |

- Claims created by the chart keep `helm.sh/resource-policy: keep`; `existingClaim` takes over
  an existing one.
- **What lives where** (measured with 8.2.1): each space is complete in the files root — `nodes/`
  with one `.mpk` metadata file per file/folder (name, parent, size, checksum, versions),
  `blobs/` with the content stored under IDs instead of names, and `trash/`. `data` holds the
  user directory (`idm`), shares and settings (`storage/metadata`, a system space), the search
  index and NATS. Without `files` enabled, all of it is on `data`.
- **To get the files out without oCIS**, the files root alone is enough (next section).
- **Back up all volumes together** to restore oCIS itself. The spaces alone are not a readable archive (content is
  stored by ID), and without `data` accounts, shares and roles are gone; without `ocis.yaml`
  nothing can be opened again. A consistent copy needs the pod stopped (or snapshots of all
  volumes at the same moment).
- Decide on `files` before the first start; oCIS does not move existing spaces.
- **NFS:** `fsGroup` does not apply, the export must let uid 1000 write. Never put `data` on
  NFS (bolt and the index rely on locks and mmap).

## Getting the files out (without oCIS)

oCIS stores content under IDs, not names, so a copy of the volume is not a folder you can browse.
[`tools/ocis-export.py`](tools/ocis-export.py) turns it back into plain folders **without a
running oCIS** — from the NAS folder, a restored backup or a copied volume. Python 3 only, no
packages:

```sh
python3 tools/ocis-export.py <storage-root> <empty-target> [--versions] [--trash]
```

- `<storage-root>` is the folder containing `spaces/` (the `files` volume; without it
  `/var/lib/ocis/storage/users` on the data volume).
- One folder per space (`personal - <name>`, project spaces by name) with the real file and
  folder names and modification times. Every file is checked against its stored SHA-1; the
  summary counts `checked` files, and any mismatch or missing content ends with exit code 1.
- `--versions` adds older versions under `_versions/`, `--trash` the trash bin under `_trash/`.
- Reads only the metadata files (`*.mpk`), not the symlinks: rclone skips symlinks by default.
  `ocis.yaml` and the data volume are **not** needed for this.
- Not exported: shares, links, accounts (they live on the data volume and mean nothing without
  oCIS). The time of a folder that never changed comes from the folder itself and is lost if
  the backup did not keep folder times.

Tested against a real oCIS 8.2.1 storage (`tests/fixtures/`): paths, content and times equal
what oCIS serves over WebDAV for the same data, also with all symlinks removed; corrupted or
missing content is reported. Re-check after oCIS upgrades that change the storage format
(`tests/test-export.py`; build a new fixture from a test pod).

## Keycloak / OIDC

Tested 2026-10-03 with Keycloak 26.7.4 and oCIS 8.2.1 in a throwaway realm: a user with role
`ocisUser` is created on the first login (graph `/me` 200, WebDAV upload 201); a user without a
role is rejected; without a token 401. Since 0.1.1 also with a **real browser** (headless
Chromium): redirect to Keycloak, login form, back in `/files/spaces/personal`, `/me` 200.

**Content-Security-Policy (fixed in 0.1.1):** the web UI fetches the IDP's discovery and token
endpoints from the browser. oCIS's built-in policy only allows `'self'` in `connect-src`, so
Chrome refused the request before it left the browser; the UI showed "We're having trouble
connecting to the login service", and neither oCIS nor the gateway logged anything. The chart
now writes the policy (`csp.directives`, oCIS defaults) to a ConfigMap and adds the issuer's
origin when `oidc.enabled`. The file replaces oCIS's policy entirely; extend `csp.directives`
for further sources. Token tests with `curl` cannot catch this: only a browser enforces a CSP.

On the Keycloak side (settings from ownCloud's example `deployments/examples/ocis_full`):

- **Client `web`**: public (no secret), Standard flow with PKCE `S256`, redirect URI
  `https://<host>/*`, web origin `https://<host>`, post-logout redirect `+`.
- **Realm roles** `ocisAdmin`, `ocisSpaceAdmin`, `ocisUser`, `ocisGuest`, assigned to the users.
- **Mapper on the client**: type *User Realm Role*, claim name `roles`, multivalued, into access
  token, ID token and userinfo. Keycloak's standard `roles` scope writes them to
  `realm_access.roles`, which oCIS does not read.

With `oidc.roleAssignment.driver: oidc` (default) the role is taken from the token on **every**
login, and a user without one of the four roles cannot log in. oCIS answers that with **500**
(log: `no roles in user claims`), not 403, and still creates the account entry. Revoking access
= removing the role in Keycloak. `driver: default` lets everybody with a Keycloak account in as
`user`.

The desktop and mobile apps of ownCloud use fixed client IDs of their own (in the same example,
`config/keycloak/clients/`); add them to the realm only when the apps are needed.

## Network: oCIS calls itself through `url`

The WebDAV service uploads to `<url>/data`, and clients receive the same URL. The **pod** must
therefore resolve the public hostname and reach the gateway with a trusted certificate
(`OCIS_INSECURE=false`), and the same for the issuer. Usually cluster DNS forwards to a resolver
that knows both names; otherwise use `hostAliases`. Symptom when it fails: uploads answer 500,
the log shows `Put "https://<host>/data": … no such host`.

## Gateway

`httpRoute` has the same shape as in the other charts: `parentRef` (or `parentRefs`) plus
`routes[]` with verbatim `rules`. Backend `<release>:9200`. Set `timeouts.request` (e.g.
`3600s`): Envoy Gateway cuts requests after 15 s, which breaks WebDAV uploads without chunking
and the server-sent events of the web UI. oCIS needs its own hostname.

## Tests

`python3 charts/ocis/tests/test-chart.py`: route and service wiring, claims and mounts, init
container, OIDC settings, the CSP (issuer origin in `connect-src`, mounted where oCIS reads it),
secrets only by reference, and the combinations that must fail. They do
not prove login or uploads; those were checked in the cluster as described above.
