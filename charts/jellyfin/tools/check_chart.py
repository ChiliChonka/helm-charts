"""Checks the invariants of the fully rendered chart, without cluster access.

Checked are properties every future version must keep (one replica, Recreate, Retain, route on
the HTTPS listener, stream timeout, data claims) — not concrete values such as the image tag or
resource numbers, which may change with every update. Rendering uses the made-up values from
ci/test-values.yaml. Extra arguments are passed to `helm template` (for counter-checks:
`python3 tools/check_chart.py --set …` must fail). Run from the chart directory.
"""
import subprocess
import sys

import yaml


def render(*args):
    result = subprocess.run(
        ["helm", "template", "jellyfin", ".", "--namespace", "jellyfin", "-f", "ci/test-values.yaml", *args],
        check=True, capture_output=True, text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def require(condition, message):
    if not condition:
        raise SystemExit(f"ERROR: {message}")


def chart_app_version():
    with open("Chart.yaml", encoding="utf-8") as f:
        return str(yaml.safe_load(f)["appVersion"])


documents = render(*sys.argv[1:])
kinds = sorted(doc["kind"] for doc in documents)
require(kinds == sorted(["Deployment", "Service", "HTTPRoute", "PersistentVolume",
                         "PersistentVolume", "PersistentVolumeClaim", "PersistentVolumeClaim"]),
        f"Unexpected resources in render: {kinds}")

deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
spec = deployment["spec"]
app = spec["template"]["spec"]["containers"][0]

# One instance on the database, never two at once (shared NFS directory).
require(spec["replicas"] == 1, "Exactly one replica: only one Jellyfin instance may use the database")
require(spec["strategy"]["type"] == "Recreate" and not spec["strategy"].get("rollingUpdate"),
        "strategy Recreate without rollingUpdate required")

# Image tag and appVersion move together.
tag = app["image"].rsplit(":", 1)[-1]
require(tag == chart_app_version(),
        f"Image tag {tag} does not match appVersion {chart_app_version()} in Chart.yaml")

# Resources: request and memory limit set, but NO CPU limit (software transcoding).
res = app.get("resources") or {}
require(res.get("requests", {}).get("cpu") and res.get("requests", {}).get("memory"),
        "CPU and memory requests must be set")
require(res.get("limits", {}).get("memory"), "Memory limit must be set")
require("cpu" not in res.get("limits", {}),
        "No CPU limit: software transcoding would be throttled")

# Probes with headroom.
require(app["livenessProbe"].get("timeoutSeconds", 1) >= 5
        and app["readinessProbe"].get("timeoutSeconds", 1) >= 5,
        "Liveness/readiness need a timeout of at least 5 s")

# Data stays where it is.
claims = {v["name"]: v["persistentVolumeClaim"]["claimName"]
          for v in spec["template"]["spec"]["volumes"] if "persistentVolumeClaim" in v}
require(claims == {"config": "jellyfin-config", "media": "jellyfin-media"},
        f"Existing data claims must be kept, found: {claims}")
pvs = [d for d in documents if d["kind"] == "PersistentVolume"]
require(all(pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain" for pv in pvs),
        "Both NFS volumes must keep Retain")

# On a gateway, on the named HTTPS listener (without sectionName it would also attach to HTTP).
route = next(d for d in documents if d["kind"] == "HTTPRoute")
parent = route["spec"]["parentRefs"][0]
require(parent.get("name") and parent.get("sectionName"),
        "Route needs a gateway and sectionName (HTTPS listener)")
require(route["spec"]["rules"][0]["timeouts"]["request"] == "10h0m0s",
        "Stream timeout of 10 h changed")

print(f"OK: 7 resources; invariants hold (image {tag}, resources {res})")
