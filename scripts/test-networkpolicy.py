#!/usr/bin/env python3
"""Render checks for the optional NetworkPolicies in front of databases and Redis; no cluster."""
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Per chart: the values that switch on database and Redis, and what must come out.
CHARTS = {
    "immich": {
        "args": ["-f", "ci/test-values.yaml"],
        "db": "immich-db",
        "redis": "immich-redis",
    },
    "paperless-ngx": {
        "args": ["--set", "cnpg.enabled=true", "--set", "cnpg.image=example/postgres:17",
                 "--set", "redisOperator.enabled=true",
                 "--set", "redisOperator.image=example/redis:7",
                 "--set", "redisOperator.existingSecret=example-redis",
                 "--set", "redisOperator.passwordKey=password"],
        "db": "paperless-db",
        "redis": "paperless-redis",
    },
    "home-assistant": {
        "args": ["-f", "ci/test-values.yaml"],
        "db": "home-assistant-db",
        "redis": None,
    },
}


def render(chart, *extra):
    cfg = CHARTS[chart]
    command = ["helm", "template", "np-test", str(ROOT / "charts" / chart), "-n", "np-test",
               *[str(ROOT / "charts" / chart / a) if a.startswith("ci/") else a for a in cfg["args"]],
               *extra]
    return subprocess.run(command, capture_output=True, text=True)


def docs(result, kind):
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if isinstance(d, dict) and d.get("kind") == kind]


def ingress_sources(policy):
    return [f for rule in policy["spec"]["ingress"] for f in rule["from"]]


for chart, cfg in CHARTS.items():
    # Off by default: a public chart cannot know the CNI or the operator namespaces.
    assert docs(render(chart), "NetworkPolicy") == [], chart

    policies = {p["metadata"]["name"]: p for p in docs(render(chart, "--set", "networkPolicy.enabled=true"), "NetworkPolicy")}
    expected = {cfg["db"]} | ({cfg["redis"]} if cfg["redis"] else set())
    assert set(policies) == expected, (chart, sorted(policies))

    db = policies[cfg["db"]]["spec"]
    assert db["podSelector"] == {"matchLabels": {"cnpg.io/cluster": cfg["db"]}}, (chart, db)
    assert db["policyTypes"] == ["Ingress"], (chart, db)
    sources = ingress_sources(policies[cfg["db"]])
    assert {"podSelector": {}} in sources, (chart, sources)
    assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "cnpg-system"}}} in sources, (chart, sources)

    if cfg["redis"]:
        redis = policies[cfg["redis"]]["spec"]
        assert redis["podSelector"] == {"matchLabels": {"app": cfg["redis"]}}, (chart, redis)
        sources = ingress_sources(policies[cfg["redis"]])
        assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "redis-operator"}}} in sources, (chart, sources)
        result = render(chart, "--set", "networkPolicy.enabled=true", "--set", "networkPolicy.redisOperatorNamespace=")
        assert result.returncode != 0 and "redisOperatorNamespace is required" in result.stderr, (chart, result.stderr)

    result = render(chart, "--set", "networkPolicy.enabled=true", "--set", "networkPolicy.cnpgOperatorNamespace=")
    assert result.returncode != 0 and "cnpgOperatorNamespace is required" in result.stderr, (chart, result.stderr)

    # Without a database there is nothing to protect.
    off = render(chart, "--set", "networkPolicy.enabled=true", "--set", "cnpg.enabled=false",
                 "--set", "cnpg.backup.enabled=false",
                 *(["--set", "redisOperator.enabled=false"] if cfg["redis"] else []))
    assert docs(off, "NetworkPolicy") == [], chart
    print(f"PASS: {chart} — {len(expected)} NetworkPolicies, off by default")

# Home Assistant in hostNetwork: Cilium policy for the node entities, only when asked for.
assert docs(render("home-assistant", "--set", "networkPolicy.enabled=true"), "CiliumNetworkPolicy") == []
cnp = docs(render("home-assistant", "--set", "networkPolicy.enabled=true",
                  "--set", "networkPolicy.ciliumHostNetworkClients=true"), "CiliumNetworkPolicy")
assert len(cnp) == 1, cnp
spec = cnp[0]["spec"]
assert spec["endpointSelector"] == {"matchLabels": {"cnpg.io/cluster": "home-assistant-db"}}, spec
assert spec["ingress"] == [{"fromEntities": ["host", "remote-node"],
                            "toPorts": [{"ports": [{"port": "5432", "protocol": "TCP"}]}]}], spec
print("PASS: home-assistant — CiliumNetworkPolicy for hostNetwork only on request")
