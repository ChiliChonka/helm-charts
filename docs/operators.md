# Prerequisites: operators and platform

The charts do **not** bring their own database, cache or ingress. They only create the custom
resources (`Cluster`, `Redis`, `HTTPRoute` …); the operators for them run once per cluster. Which
chart needs what is listed in the table in the [README](../README.md).

Principle: **one operator per service, cluster-wide; one instance per app, in the app's
namespace.** Databases and caches are not shared between apps — an update, an outage or a restore
only affects that one app.

Tested versions (as of 2026-09-27). Always install with a pinned version; a run without
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

## cert-manager

For the Barman plugin and for certificates on the gateway. Tested v1.21.2.
