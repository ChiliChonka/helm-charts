#!/usr/bin/env bash
# Checks all charts without a cluster: dependencies, lint, render with defaults and with made-up
# CI values, chart-specific tests. Identical locally (`task check`) and in CI.
set -euo pipefail
cd "$(dirname "$0")/.."

# Upstream repositories of the dependencies (Chart.lock has the URLs; Helm must know http repos).
helm repo add jellyfin https://jellyfin.github.io/jellyfin-helm >/dev/null
helm repo add pajikos https://pajikos.github.io/home-assistant-helm-chart >/dev/null
helm repo add tika https://apache.jfrog.io/artifactory/tika >/dev/null
helm repo add maikumori https://maikumori.github.io/helm-charts >/dev/null
helm repo update >/dev/null

for chart in charts/*/; do
  name=$(basename "$chart")
  echo "== $name"
  # The tika index contains invalid entries (3.2.2.0); Helm warns about them on every run.
  helm dependency build "$chart" 2> >(grep -v "skipping loading invalid entry" >&2) >/dev/null
  # Defaults only. A chart without usable defaults (e.g. a public URL) names the values you must
  # set in ci/required-values.yaml; its own tests check that a render without them fails.
  required=()
  if [ -f "$chart/ci/required-values.yaml" ]; then required=(-f "$chart/ci/required-values.yaml"); fi
  helm lint "$chart" --strict --quiet "${required[@]}"
  helm template "$name" "$chart" "${required[@]}" >/dev/null
  if [ -f "$chart/ci/test-values.yaml" ]; then
    helm lint "$chart" --strict --quiet -f "$chart/ci/test-values.yaml"
    helm template "$name" "$chart" -f "$chart/ci/test-values.yaml" >/dev/null
  fi
done

echo "== chart-specific tests"
(cd charts/jellyfin && python3 tools/check_chart.py)
python3 charts/home-assistant/tests/test-backup.py
python3 charts/paperless-ngx/tests/test-admin-bootstrap.py
python3 charts/paperless-ngx/tests/test-v3.py
python3 charts/immich/tests/test-chart.py
python3 charts/ocis/tests/test-chart.py
echo "OK: all charts"
