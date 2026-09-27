# helm-charts

Wrapper-Charts für selbst betriebene Apps auf Kubernetes: das Upstream-Chart der App, dazu das,
was im Eigenbetrieb fehlt — Datenbank und Cache über Operatoren statt eingebetteter Subcharts,
Backups nach S3, eine `HTTPRoute` für die Gateway API, optional Daten von einer NFS-Freigabe.

| Chart | Verzeichnis | App | braucht | optional |
|---|---|---|---|---|
| `jellyfin-helm-chart` | [`charts/jellyfin`](charts/jellyfin) | [Jellyfin](https://jellyfin.org) 12.1, Upstream [jellyfin-helm](https://github.com/jellyfin/jellyfin-helm) | — | NFS, Gateway API |
| `home-assistant-helm-chart` | [`charts/home-assistant`](charts/home-assistant) | [Home Assistant](https://www.home-assistant.io), Upstream [pajikos](https://github.com/pajikos/home-assistant-helm-chart) | — | CNPG (Recorder-DB) + Barman-Plugin, NFS, Gateway API |
| `paperless-ngx` | [`charts/paperless-ngx`](charts/paperless-ngx) | [paperless-ngx](https://docs.paperless-ngx.com) 3.2, mit Tika und Gotenberg | CNPG, redis-operator | Barman-Plugin, Gateway API |

Installation der Operatoren und getestete Versionen: [`docs/operators.md`](docs/operators.md).

## Installieren

Die Charts liegen als OCI-Artefakte in der GitHub Container Registry:

```bash
helm install jellyfin oci://ghcr.io/chilichonka/charts/jellyfin-helm-chart --version 1.1.0 \
  -n jellyfin --create-namespace -f my-values.yaml
```

Die Defaults sind neutral und ohne Umgebungsbezug: kein NFS, keine Route, keine Datenbank. Was man
einschaltet, steht in der `values.yaml` des Charts, dort mit Beispielen. Erfundene, vollständige
Beispiele: `charts/*/ci/test-values.yaml`.

**Secrets** stehen nie in den Werten. Die Charts verweisen nur auf vorhandene Secrets
(`credentialsSecret`, `existingSecret` …) oder nehmen die, die die Operatoren erzeugen (CNPG legt
`<cluster>-app` mit den Zugangsdaten an).

## Beispiele

**Jellyfin mit Medien von NFS**, erreichbar über ein Gateway:

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
          timeouts: {request: 10h0m0s}     # Streams
```

**Home Assistant mit Recorder-Datenbank in CNPG:**

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

dazu in `configuration.yaml`: `recorder: db_url: !env_var HA_RECORDER_DB_URL`.

## Eigenheiten, die man kennen sollte

- **jellyfin** läuft mit `Recreate` und genau einer Replik: Zwei Instanzen auf derselben Datenbank
  beschädigen sie. Keine CPU-Grenze — Software-Transcoding würde sonst gedrosselt.
- **home-assistant** braucht hinter einem Gateway `trusted_proxies`; die Defaults decken die
  privaten Netze ab. `ingress.external: true` bleibt gesetzt, obwohl kein Ingress läuft — nur so
  legt das Subchart die Proxy-Einstellung bei einer Neuinstallation an.
- **paperless-ngx:** Alle Einstellungen der App (`paperless-ngx.paperlessVars`) sind in der
  [offiziellen Doku](https://docs.paperless-ngx.com/configuration/) beschrieben.
- **paperless-ngx 3** braucht `PAPERLESS_SECRET_KEY` (das Chart erzeugt ihn einmal und behält ihn);
  entfernte Einstellungen aus Version 2 lehnt das Chart beim Rendern ab.

## Entwicklung

```bash
task check        # oder ./scripts/check.sh — lint, Render mit Defaults und CI-Werten, Tests
```

Release eines Charts: Version in `Chart.yaml` anheben, Tag `<verzeichnis>-v<version>` pushen
(z. B. `jellyfin-v1.1.0`); die CI prüft, dass Tag und `Chart.yaml` übereinstimmen, und schiebt das
Paket nach `oci://ghcr.io/chilichonka/charts`.

## Lizenz

[Apache-2.0](LICENSE). Die eingebundenen Upstream-Charts behalten ihre eigenen Lizenzen.
