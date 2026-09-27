# Voraussetzungen: Operatoren und Plattform

Die Charts bringen ihre Datenbank, ihren Cache und ihren Eingang **nicht** selbst mit. Sie legen
nur die Custom Resources an (`Cluster`, `Redis`, `HTTPRoute` …); die Operatoren dafür laufen einmal
im Cluster. Welches Chart was braucht, steht in der Tabelle im [README](../README.md).

Grundsatz: **ein Operator je Dienst, cluster-weit, eine Instanz je App im Namespace der App.**
Datenbanken und Caches werden nicht zwischen Apps geteilt — ein Update, ein Ausfall oder ein
Restore trifft nur die eine App.

Getestete Versionen (Stand 2026-09-27). Immer mit fester Version installieren; ein Lauf ohne
`--version` nimmt stillschweigend das Neueste.

## CloudNativePG (Postgres)

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm upgrade --install cnpg cnpg/cloudnative-pg --version 0.29.0 \
  -n cnpg-system --create-namespace --wait        # Operator 1.30.0
```

Das Chart bringt seine CRDs selbst mit (`crds.create: true`). CNPG bestimmt, welche
Kubernetes-Versionen tragen — vor einem Kubernetes-Upgrade die Support-Matrix prüfen.

### Barman-Cloud-Plugin (Backups nach S3)

Für `cnpg.backup.enabled` in den Charts. Setzt **cert-manager** voraus (das Plugin spricht mit dem
Operator über TLS).

```bash
kubectl apply -f https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/v0.15.0/manifest.yaml
kubectl -n cnpg-system rollout status deploy/barman-cloud
```

Das eingebaute Barman (`Cluster.spec.backup.barmanObjectStore`) ist seit CNPG 1.26 abgekündigt und
fällt mit 1.31 weg; die Charts nutzen deshalb nur das Plugin (`ObjectStore` + `ScheduledBackup`).

**Hinweis für Knoten-Wartung:** Ein CNPG-Cluster mit einer Instanz blockiert `kubectl drain`
(PodDisruptionBudget). Vorher `spec.nodeMaintenanceWindow: {inProgress: true, reusePVC: true}`
setzen, danach wieder entfernen — siehe CNPG-Doku „Kubernetes upgrade and maintenance“.

## S3 (Ziel der Backups)

Jeder S3-kompatible Speicher. Die Charts erwarten nur `cnpg.backup.endpointURL`, den Bucket
(`destinationPath`) und ein Secret mit `ACCESS_KEY_ID`/`SECRET_ACCESS_KEY`
(`cnpg.backup.credentialsSecret`). Den Bucket legt weder CNPG noch das Plugin an.

## Redis (OT-CONTAINER-KIT redis-operator)

Für paperless-ngx (`redisOperator.enabled`, Celery-Broker ohne Persistenz).

```bash
helm repo add ot-helm https://ot-container-kit.github.io/helm-charts/
helm upgrade --install redis-operator ot-helm/redis-operator --version 0.26.1 \
  -n redis-operator --create-namespace --wait     # Operator v0.26.0
```

- **Helm aktualisiert CRDs aus `crds/` nicht.** Bei einem Operator-Update die CRDs vorher aus dem
  neuen Chart mit `kubectl apply --server-side` einspielen.
- Ohne `kubernetesConfig.redisSecret` hat eine `Redis`-Instanz **kein Passwort** — jeder Pod im
  Cluster erreicht sie. Das paperless-Chart erzeugt deshalb ein Passwort-Secret.
- Die Images (`quay.io/opstree/redis`) mit festem Tag verwenden.

## Keycloak-Operator (optional)

Keines der Charts braucht Keycloak zwingend. Für Single Sign-on: Keycloak liefert den Operator
nur als Manifeste (kein Helm-Chart), per Default auf den eigenen Namespace beschränkt.

```bash
V=26.7.4
B=https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/$V/kubernetes
for c in keycloaks keycloakrealmimports keycloakoidcclients keycloaksamlclients; do
  kubectl apply -f $B/$c.k8s.keycloak.org-v1.yml
done
kubectl create namespace keycloak
kubectl -n keycloak apply -f $B/kubernetes.yml
```

Ab 26.7 kommen zwei CRDs dazu (`keycloakoidcclients`, `keycloaksamlclients`); ohne sie startet der
Operator nicht.
Für einen Operator, der alle Namespaces beobachtet, liefert Keycloak im selben Repo eine eigene
Variante unter `kubernetes/cluster-wide/`.

## Eingang: Gateway API

Die Charts veröffentlichen sich über eine `HTTPRoute` (`httpRoute.*`), kein Ingress. Getestet mit
**Envoy Gateway v1.9.1**; jede Gateway-API-Implementierung mit HTTPRoute sollte gehen.
`httpRoute.parentRef.sectionName` muss auf den HTTPS-Listener zeigen, sonst hängt die Route auch am
HTTP-Listener. Envoy begrenzt Anfragen ohne `timeouts.request` auf 15 s — Uploads und Streams
brauchen einen eigenen Wert (jellyfin: 10 h).

## NFS (optional)

jellyfin und home-assistant können ihre Daten von einer bestehenden NFS-Freigabe nehmen
(`nfs.*`): Das Chart legt dann statische PersistentVolumes mit `Retain` an. Für **dynamische**
NFS-Volumes stattdessen `csi-driver-nfs` (getestet 4.13.4) mit einer StorageClass.

## cert-manager

Für das Barman-Plugin und für Zertifikate am Gateway. Getestet v1.21.2.
