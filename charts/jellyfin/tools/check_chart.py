"""Prüft die Invarianten des vollständig gerenderten Charts, ohne Cluster-Zugriff.

Geprüft werden Eigenschaften, die jede künftige Version erfüllen muss (eine Replik, Recreate,
Retain, Route am HTTPS-Listener, Stream-Timeout, Daten-Claims) — keine konkreten Werte wie
Image-Tag oder Ressourcenzahlen, die sich mit jedem Update ändern dürfen. Gerendert wird mit den
erfundenen Werten aus ci/test-values.yaml. Zusätzliche Argumente gehen an `helm template` (für
Gegenproben: `python3 tools/check_chart.py --set …` muss scheitern). Aufruf im Chart-Verzeichnis.
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
        raise SystemExit(f"FEHLER: {message}")


def chart_app_version():
    with open("Chart.yaml", encoding="utf-8") as f:
        return str(yaml.safe_load(f)["appVersion"])


documents = render(*sys.argv[1:])
kinds = sorted(doc["kind"] for doc in documents)
require(kinds == sorted(["Deployment", "Service", "HTTPRoute", "PersistentVolume",
                         "PersistentVolume", "PersistentVolumeClaim", "PersistentVolumeClaim"]),
        f"Unerwartete Ressourcen im Render: {kinds}")

deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
spec = deployment["spec"]
app = spec["template"]["spec"]["containers"][0]

# Eine Instanz auf der Datenbank, und nie zwei gleichzeitig (gemeinsames NFS-Verzeichnis).
require(spec["replicas"] == 1, "Genau eine Replik: nur eine Jellyfin-Instanz darf die Datenbank nutzen")
require(spec["strategy"]["type"] == "Recreate" and not spec["strategy"].get("rollingUpdate"),
        "strategy Recreate ohne rollingUpdate erforderlich")

# Image-Tag und appVersion laufen gemeinsam (README, Versionierung).
tag = app["image"].rsplit(":", 1)[-1]
require(tag == chart_app_version(),
        f"Image-Tag {tag} passt nicht zu appVersion {chart_app_version()} in Chart.yaml")

# Ressourcen: Anfrage und Speichergrenze gesetzt, aber KEINE CPU-Grenze (Software-Transcoding).
res = app.get("resources") or {}
require(res.get("requests", {}).get("cpu") and res.get("requests", {}).get("memory"),
        "CPU- und Speicher-Anfrage müssen gesetzt sein")
require(res.get("limits", {}).get("memory"), "Speichergrenze muss gesetzt sein")
require("cpu" not in res.get("limits", {}),
        "Keine CPU-Grenze: Software-Transcoding würde gedrosselt (README, Ressourcen)")

# Checks mit Reserve.
require(app["livenessProbe"].get("timeoutSeconds", 1) >= 5
        and app["readinessProbe"].get("timeoutSeconds", 1) >= 5,
        "Liveness/Readiness brauchen mindestens 5 s Timeout")

# Daten bleiben, wo sie sind.
claims = {v["name"]: v["persistentVolumeClaim"]["claimName"]
          for v in spec["template"]["spec"]["volumes"] if "persistentVolumeClaim" in v}
require(claims == {"config": "jellyfin-config", "media": "jellyfin-media"},
        f"Bestehende Daten-Claims müssen erhalten bleiben, gefunden: {claims}")
pvs = [d for d in documents if d["kind"] == "PersistentVolume"]
require(all(pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain" for pv in pvs),
        "Beide NFS-Volumes müssen Retain behalten")

# Am Gateway, und zwar am benannten HTTPS-Listener (ohne sectionName hinge sie auch am HTTP-Listener).
route = next(d for d in documents if d["kind"] == "HTTPRoute")
parent = route["spec"]["parentRefs"][0]
require(parent.get("name") and parent.get("sectionName"),
        "Route braucht Gateway und sectionName (HTTPS-Listener)")
require(route["spec"]["rules"][0]["timeouts"]["request"] == "10h0m0s",
        "Stream-Timeout von 10 h verändert")

print(f"OK: 7 Ressourcen; Invarianten erfüllt (Image {tag}, Ressourcen {res})")
