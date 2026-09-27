#!/usr/bin/env bash
# Prüft alle Charts ohne Cluster: Abhängigkeiten, lint, Render mit Defaults und mit erfundenen
# CI-Werten, chart-eigene Tests. Lokal (`task check`) und in der CI identisch.
set -euo pipefail
cd "$(dirname "$0")/.."

# Upstream-Repos der Abhängigkeiten (Chart.lock nennt die URLs; http-Repos muss Helm kennen).
helm repo add jellyfin https://jellyfin.github.io/jellyfin-helm >/dev/null
helm repo add pajikos https://pajikos.github.io/home-assistant-helm-chart >/dev/null
helm repo add tika https://apache.jfrog.io/artifactory/tika >/dev/null
helm repo add maikumori https://maikumori.github.io/helm-charts >/dev/null
helm repo update >/dev/null

for chart in charts/*/; do
  name=$(basename "$chart")
  echo "== $name"
  # Der tika-Index enthält ungültige Einträge (3.2.2.0); Helm warnt dazu bei jedem Lauf.
  helm dependency build "$chart" 2> >(grep -v "skipping loading invalid entry" >&2) >/dev/null
  helm lint "$chart" --strict --quiet
  helm template "$name" "$chart" >/dev/null                       # Defaults allein
  if [ -f "$chart/ci/test-values.yaml" ]; then
    helm lint "$chart" --strict --quiet -f "$chart/ci/test-values.yaml"
    helm template "$name" "$chart" -f "$chart/ci/test-values.yaml" >/dev/null
  fi
done

echo "== Chart-eigene Tests"
(cd charts/jellyfin && python3 tools/check_chart.py)
python3 charts/paperless-ngx/tests/test-admin-bootstrap.py
python3 charts/paperless-ngx/tests/test-v3.py
echo "OK: alle Charts"
