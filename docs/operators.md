# Prerequisites: operators and platform

The charts do **not** bring their own database, cache or ingress. They only create the custom
resources (`Cluster`, `Redis`, `HTTPRoute` …); the operators for them run once per cluster. Which
chart needs what is listed in the table in the [README](../README.md).

Principle: **one operator per service, cluster-wide; one instance per app, in the app's
namespace.** Databases and caches are not shared between apps — an update, an outage or a restore
only affects that one app.

## Why operators instead of bundled subcharts

Most app charts can bring their own Postgres or Redis as a subchart. For a cluster you run
yourself for years, that turned out to be the weaker choice:

- **Bundled images age silently.** The Bitnami subcharts that many charts depended on point to
  image tags that Docker Hub no longer serves (`404`). A running pod does not show it — it keeps
  running from the node's image cache until it is rescheduled to another node, and then fails
  with `ImagePullBackOff`, typically during an outage. Check tags at the registry, not at the pod.
- **A database needs a lifecycle, not just a pod.** An operator brings backups (base backup plus
  continuous WAL archiving), point-in-time recovery into a new cluster, failover between
  instances, minor-version upgrades, and a documented node-maintenance procedure. A bundled
  StatefulSet brings none of that; you would script it per app.
- **One place to update.** The operator is installed and upgraded once, with a pinned version.
  Each app only describes *what* it needs (a `Cluster`, a `Redis`), not *how* to run it.
- **Isolation stays per app.** Every app gets its own instance in its own namespace, so a restore
  of one app's database never touches another app.
- **Credentials are generated, not configured.** CloudNativePG creates `<cluster>-app` with user,
  password and URI; the charts read from it. No database password ever appears in values.

The price: operators are cluster-wide infrastructure. Their CRDs outlive any single app, and an
operator upgrade touches every instance at once (CloudNativePG 1.30 rolled all clusters in about
2.5 minutes). Plan operator upgrades like cluster upgrades.

## Installation order

1. **cert-manager** — the Barman plugin (and usually the gateway) needs it.
2. **CloudNativePG**, then the **Barman Cloud plugin**.
3. An **S3 target** for backups (existing S3, or an operator-managed one below), and the bucket.
4. **redis-operator** if you install paperless-ngx.
5. **Gateway API** implementation and a Gateway with an HTTPS listener.
6. The app charts.

Tested versions (as of 2026-09-28). Always install with a pinned version; a run without
`--version` silently takes the latest.

## CloudNativePG (Postgres)

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm upgrade --install cnpg cnpg/cloudnative-pg --version 0.29.0 \
  -n cnpg-system --create-namespace --wait        # operator 1.30.0
```

The chart ships its own CRDs (`crds.create: true`). CloudNativePG determines which Kubernetes
versions are supported — check its support matrix before a Kubernetes upgrade.

### Barman Cloud plugin (backups to S3)

Needed for `cnpg.backup.enabled` in the charts. Requires **cert-manager** (the plugin talks to the
operator over TLS).

```bash
kubectl apply -f https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/v0.15.0/manifest.yaml
kubectl -n cnpg-system rollout status deploy/barman-cloud
```

The built-in Barman support (`Cluster.spec.backup.barmanObjectStore`) is deprecated since
CloudNativePG 1.26 and goes away with 1.31; the charts therefore only use the plugin
(`ObjectStore` + `ScheduledBackup`).

**Node maintenance:** a CloudNativePG cluster with a single instance blocks `kubectl drain`
(PodDisruptionBudget). Set `spec.nodeMaintenanceWindow: {inProgress: true, reusePVC: true}`
before draining and remove it afterwards — see the CloudNativePG documentation “Kubernetes
upgrade and maintenance”.

## S3 (backup target)

Any S3-compatible storage. The charts only need `cnpg.backup.endpointURL`, the bucket
(`destinationPath`) and a Secret with `ACCESS_KEY_ID`/`SECRET_ACCESS_KEY`
(`cnpg.backup.credentialsSecret`). Neither CloudNativePG nor the plugin creates the bucket.

Point `endpointURL` at the **in-cluster Service** of your S3, not at a public hostname: otherwise
WAL archiving depends on your ingress, and any change there silently stops it.

### Operator-managed S3 with Garage

MinIO's community repositories (server and operator) are archived. A maintained alternative that
fits the operator model is [Garage](https://garagehq.deuxfleurs.fr) with the
[garage-operator](https://github.com/rajsinghtech/garage-operator): the service is a
`GarageCluster`, and each app gets its bucket and key as `GarageBucket` / `GarageKey` in its own
namespace; the operator writes the credentials into a Secret there. Tested: garage-operator chart
0.7.12, Garage v2.4.1.

```bash
helm upgrade --install garage-operator oci://ghcr.io/rajsinghtech/charts/garage-operator \
  --version 0.7.12 -n garage-operator-system --create-namespace --wait
```

- **Which namespaces may create buckets** is controlled by a `GarageReferenceGrant` next to the
  `GarageCluster`.
- **Secret key names:** the Secret of a `GarageKey` contains `access-key-id` and
  `secret-access-key`; map them in the chart values (`cnpg.backup.accessKeyIdKey`,
  `cnpg.backup.secretAccessKeyKey`; the defaults expect `ACCESS_KEY_ID`/`SECRET_ACCESS_KEY`).
  Consumers that expect other names (Longhorn wants `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_ENDPOINTS`) get them via `spec.secretTemplate` on the `GarageKey`.
- **Set the region** (`cnpg.backup.regionKey: region`; the Secret of a `GarageKey` has that key).
  Garage checks the region in the request signature. Without it the Barman client signs with
  `us-east-1` and gets `400 … unexpected scope`. A running cluster hides this: the client reads
  the region from the error body and retries. The first check of a **new or restored** archive
  is a `HEAD` without a body, so it fails (`Bad request when accessing bucket`,
  `ContinuousArchiving=False`). The reason is in the Garage log, not in the sidecar.
- **Metadata never on NFS.** Garage keeps its metadata in LMDB, which is not safe on NFS. Put the
  metadata volume on block storage and the data volume on NFS if you like.
- **Back up the metadata outside of Garage.** With `replication.factor: 1`, losing the metadata
  loses every object, including your database backups; it cannot be rebuilt from the data blocks.
  Set `storage.metadataAutoSnapshotInterval` (e.g. `6h`; Garage keeps the two latest consistent
  snapshots under `<metadata_dir>/snapshots/`) and copy the metadata volume to a target that does
  **not** depend on Garage (e.g. a Longhorn backup target on NFS).
- **Restoring a snapshot (Garage v2.4.1, LMDB):** a snapshot is a directory containing a single
  **file** `db.lmdb` — that file is the `data.mdb`. `cp -r snapshots/<ts> db.lmdb`, as the
  Garage documentation suggests, produces `db.lmdb/db.lmdb`; Garage then starts without an error
  on an **empty** database. Correct, with Garage stopped:

  ```bash
  cd <metadata_dir>
  mv db.lmdb db.lmdb.old
  mkdir db.lmdb
  cp snapshots/<timestamp>/db.lmdb db.lmdb/data.mdb
  ```

  Then start Garage and run `garage repair -a --yes tables`. To stop it, scale the operator to 0
  first, then the StatefulSet — otherwise the operator scales it back up.

## Redis (OT-CONTAINER-KIT redis-operator)

For paperless-ngx (`redisOperator.enabled`, Celery broker without persistence).

```bash
helm repo add ot-helm https://ot-container-kit.github.io/helm-charts/
helm upgrade --install redis-operator ot-helm/redis-operator --version 0.26.1 \
  -n redis-operator --create-namespace --wait     # operator v0.26.0
```

- **Helm does not update CRDs from `crds/`.** When upgrading the operator, apply the CRDs from the
  new chart first with `kubectl apply --server-side`.
- Without `kubernetesConfig.redisSecret` a `Redis` instance has **no password** — every pod in the
  cluster can reach it. The paperless chart therefore generates a password Secret.
- Use the images (`quay.io/opstree/redis`) with a pinned tag.

## Keycloak operator (optional)

None of the charts requires Keycloak. For single sign-on: Keycloak ships the operator only as
manifests (no Helm chart), watching its own namespace by default.

```bash
V=26.7.4
B=https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/$V/kubernetes
for c in keycloaks keycloakrealmimports keycloakoidcclients keycloaksamlclients; do
  kubectl apply -f $B/$c.k8s.keycloak.org-v1.yml
done
kubectl create namespace keycloak
kubectl -n keycloak apply -f $B/kubernetes.yml
```

Version 26.7 added two CRDs (`keycloakoidcclients`, `keycloaksamlclients`); the operator does not
start without them. For an operator that watches all namespaces, Keycloak provides a separate
variant in the same repository under `kubernetes/cluster-wide/`.

**Operator in its own namespace, watching a list of app namespaces** (the model of this repo:
one operator, instances in the apps' namespaces):

- In the Deployment, set the four `QUARKUS_OPERATOR_SDK_CONTROLLERS_<CONTROLLER>_NAMESPACES`
  variables to a comma-separated list (e.g. `app-a,app-b`) instead of `JOSDK_WATCH_CURRENT`.
- Bind the ClusterRoles **inside each watched namespace** with RoleBindings whose subject is the
  operator's ServiceAccount in its own namespace: `view`, `keycloakcontroller-cluster-role`,
  `keycloakrealmimportcontroller-cluster-role`, `keycloakoidcclientcontroller-cluster-role`,
  `keycloaksamlclientcontroller-cluster-role`. Without them the operator logs `forbidden`.
  In its own namespace the operator needs nothing besides ServiceAccount, Service and Deployment.
- **Moving an existing operator** there is safe: the Keycloak objects belong to the `Keycloak`
  CR, not to the operator. Measured: StatefulSet, pod, services, secrets and the realm-import job
  kept their UID and resourceVersion, the realm import did not run again, Keycloak did not
  restart. Scale the old operator to 0 before starting the new one, so that two operators never
  reconcile the same CR.
- Keep CRDs and ClusterRoles out of any Helm release that might be deleted: they would take every
  Keycloak CR with them.

## Ingress: Gateway API

The charts are exposed through an `HTTPRoute` (`httpRoute.*`), not an Ingress. Tested with
**Envoy Gateway v1.9.1**; any Gateway API implementation supporting HTTPRoute should work.
`httpRoute.parentRef.sectionName` must point to the HTTPS listener, otherwise the route also
attaches to the HTTP listener. Envoy Gateway limits requests to 15 s unless `timeouts.request` is
set — uploads and streams need their own value (jellyfin: 10 h).

## NFS (optional)

jellyfin and home-assistant can take their data from an existing NFS export (`nfs.*`): the chart
then creates static PersistentVolumes with `Retain`. For **dynamic** NFS volumes use
`csi-driver-nfs` (tested 4.13.4) with a StorageClass instead.

## Storage: Longhorn under CloudNativePG

CloudNativePG replicates itself; a Longhorn volume with three replicas under a two-instance
cluster stores every byte six times. Tested with Longhorn 1.12.1:

- A StorageClass with **one replica** for CloudNativePG clusters with at least two instances, and
  `affinity.podAntiAffinityType: required` on the `Cluster` so both instances never share a node.
- Use `dataLocality: best-effort`, **not** `strict-local` (which CloudNativePG's documentation
  suggests): a strict-local volume cannot attach on another node, so `kubectl drain` hangs
  (longhorn/longhorn#8753). With `best-effort` plus the Longhorn setting `node-drain-policy:
  block-for-eviction-if-contains-last-replica` (Helm value `defaultSettings.nodeDrainPolicy`),
  Longhorn copies the last replica away and then lets the drain continue — tested with a real
  drain: 37 s, data intact on the new node.
- Lowering `numberOfReplicas` on an existing volume does **not** remove the surplus replica;
  delete it yourself (not the one on the pod's node) and check the volume stays `healthy`.
- **Block multipath for Longhorn's devices.** Longhorn exposes volumes as iSCSI disks (`/dev/sdX`,
  vendor `IET VIRTUAL-DISK`). If `multipathd` grabs one, the pod hangs in `Init` with `MountDevice
  failed … already mounted or mount point busy`. In `/etc/multipath.conf`:
  `blacklist { devnode "^sd[a-z0-9]+" }` — only if your system disks are not multipath devices.
- For migrating an existing CloudNativePG instance to another StorageClass: change
  `storage.storageClass`, then `kubectl cnpg destroy <cluster> <n>` for the replica (it is rebuilt
  under the **same** name on the new class), switch over, and repeat for the former primary.

## cert-manager

For the Barman plugin and for certificates on the gateway. Tested v1.21.2.
