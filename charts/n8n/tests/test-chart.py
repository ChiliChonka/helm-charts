#!/usr/bin/env python3
"""Offline Helm integration checks for the n8n wrapper chart; no live cluster or real credentials."""
from pathlib import Path
import subprocess
import tarfile
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1]
FIXTURE = CHART / "ci/test-values.yaml"


def render(*args, fixture=True, release="flow-test"):
    command = ["helm", "template", release, str(CHART), "-n", "flow-test"]
    if fixture:
        command += ["-f", str(FIXTURE)]
    return subprocess.run(command + list(args), text=True, capture_output=True)


def env_of(container):
    out = {}
    for e in container.get("env", []):
        if "value" in e:
            out[e["name"]] = e["value"]
        else:
            ref = e["valueFrom"].get("secretKeyRef") or e["valueFrom"].get("configMapKeyRef")
            out[e["name"]] = ("secret" if "secretKeyRef" in e["valueFrom"] else "cm", ref["name"], ref["key"])
    return out


class ChartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = render()
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.docs = [d for d in yaml.safe_load_all(result.stdout) if d]

    def resource(self, kind, name=None):
        matches = [d for d in self.docs if d["kind"] == kind
                   and (name is None or d["metadata"]["name"] == name)]
        self.assertEqual(len(matches), 1, (kind, name, [d["metadata"]["name"] for d in self.docs if d["kind"] == kind]))
        return matches[0]

    def configmap_value(self, ref):
        kind, name, key = ref
        self.assertEqual(kind, "cm")
        return self.resource("ConfigMap", name)["data"][key]

    def test_app_version_matches_upstream(self):
        chart = yaml.safe_load(open(CHART / "Chart.yaml"))
        dep = chart["dependencies"][0]
        archive = CHART / "charts" / f"{dep['name']}-{dep['version']}.tgz"
        with tarfile.open(archive) as tar:
            upstream = yaml.safe_load(tar.extractfile(f"{dep['name']}/Chart.yaml"))
        self.assertEqual(chart["appVersion"], upstream["appVersion"])
        main = self.resource("Deployment", "flow-test-n8n-main")
        image = main["spec"]["template"]["spec"]["containers"][0]["image"]
        self.assertTrue(image.endswith(":" + chart["appVersion"]), image)

    def test_route_targets_real_services(self):
        route = self.resource("HTTPRoute")["spec"]
        self.assertEqual(route["parentRefs"][0]["sectionName"], "https")
        targets = {}  # path -> (service, deployment the service selects)
        deployments = [d for d in self.docs if d["kind"] == "Deployment"]
        for rule in route["rules"]:
            for backend in rule["backendRefs"]:
                service = self.resource("Service", backend["name"])
                self.assertIn(backend["port"], [p["port"] for p in service["spec"]["ports"]])
                selected = [d["metadata"]["name"] for d in deployments
                            if all(d["spec"]["template"]["metadata"]["labels"].get(k) == v
                                   for k, v in service["spec"]["selector"].items())]
                self.assertEqual(len(selected), 1, (backend["name"], selected))
                for m in rule["matches"]:
                    targets[m["path"]["value"]] = selected[0]
        self.assertEqual(targets["/"], "flow-test-n8n-main")
        for path in ("/webhook/", "/webhook-waiting/", "/form/", "/form-waiting/", "/mcp/"):
            self.assertEqual(targets[path], "flow-test-n8n-webhook-processor", path)

    def test_webhook_pods_and_main(self):
        hook = self.resource("Deployment", "flow-test-n8n-webhook-processor")["spec"]
        self.assertEqual(hook["replicas"], 1)
        env = env_of(hook["template"]["spec"]["containers"][0])
        self.assertEqual(env["DB_POSTGRESDB_PASSWORD"], ("secret", "n8n-db-app", "password"))
        self.assertEqual(env["N8N_ENCRYPTION_KEY"], ("secret", "n8n-core", "N8N_ENCRYPTION_KEY"))
        self.assertIs(hook["template"]["spec"]["automountServiceAccountToken"], False)
        # Main hands production webhooks over to the webhook pods.
        main = env_of(self.resource("Deployment", "flow-test-n8n-main")["spec"]["template"]["spec"]["containers"][0])
        disable = main["N8N_DISABLE_PRODUCTION_MAIN_PROCESS"]
        value = disable if isinstance(disable, str) else self.configmap_value(disable)
        self.assertEqual(value, "true")

    def test_missing_webhook_path_fails(self):
        values = yaml.safe_load(open(FIXTURE))
        rule = values["httpRoute"]["routes"][0]["rules"][1]
        rule["matches"] = [m for m in rule["matches"] if m["path"]["value"] != "/mcp/"]
        broken = CHART / "tests" / "_missing-mcp.yaml"
        broken.write_text(yaml.safe_dump(values))
        try:
            result = subprocess.run(["helm", "template", "flow-test", str(CHART), "-n", "flow-test",
                                     "-f", str(broken)], text=True, capture_output=True)
        finally:
            broken.unlink()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no httpRoute rule sends /mcp/ to the Service <release>-webhook-processor", result.stderr)
        # Sent to main instead of the webhook pods: also refused.
        values = yaml.safe_load(open(FIXTURE))
        values["httpRoute"]["routes"][0]["rules"][1]["backendRefs"][0]["name"] = "flow-test-n8n-main"
        broken.write_text(yaml.safe_dump(values))
        try:
            result = subprocess.run(["helm", "template", "flow-test", str(CHART), "-n", "flow-test",
                                     "-f", str(broken)], text=True, capture_output=True)
        finally:
            broken.unlink()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no httpRoute rule sends /webhook/", result.stderr)

    def test_database_and_redis_wiring(self):
        cluster = self.resource("Cluster")
        redis = self.resource("Redis")
        for name in ("flow-test-n8n-main", "flow-test-n8n-worker"):
            env = env_of(self.resource("Deployment", name)["spec"]["template"]["spec"]["containers"][0])
            self.assertEqual(self.configmap_value(env["DB_POSTGRESDB_HOST"]), cluster["metadata"]["name"] + "-rw")
            self.assertEqual(self.configmap_value(env["DB_POSTGRESDB_USER"]), cluster["spec"]["bootstrap"]["initdb"]["owner"])
            self.assertEqual(self.configmap_value(env["DB_POSTGRESDB_DATABASE"]), cluster["spec"]["bootstrap"]["initdb"]["database"])
            self.assertEqual(env["DB_POSTGRESDB_PASSWORD"], ("secret", cluster["metadata"]["name"] + "-app", "password"))
            self.assertEqual(self.configmap_value(env["QUEUE_BULL_REDIS_HOST"]), redis["metadata"]["name"])
            secret = redis["spec"]["kubernetesConfig"]["redisSecret"]
            self.assertEqual(env["QUEUE_BULL_REDIS_PASSWORD"], ("secret", secret["name"], secret["key"]))
            self.assertEqual(self.configmap_value(env["EXECUTIONS_MODE"]), "queue")
            self.assertEqual(env["N8N_ENCRYPTION_KEY"], ("secret", "n8n-core", "N8N_ENCRYPTION_KEY"))
            self.assertEqual(self.configmap_value(env["N8N_EDITOR_BASE_URL"]), "https://flows.example.com")
            self.assertEqual(self.configmap_value(env["N8N_WEBHOOK_URL"]), "https://flows.example.com")
        self.assertFalse(cluster["spec"]["enableSuperuserAccess"])
        self.assertEqual(cluster["spec"]["plugins"][0]["parameters"]["barmanObjectName"],
                         self.resource("ObjectStore")["metadata"]["name"])

    def test_no_secret_material_rendered(self):
        # Encryption key and runner token come from n8n-core; the chart renders no Secret at all.
        self.assertEqual([d["metadata"]["name"] for d in self.docs if d["kind"] == "Secret"], [])
        worker = self.resource("Deployment", "flow-test-n8n-worker")["spec"]["template"]["spec"]
        runner = [c for c in worker["containers"] if c["name"] == "task-runner"]
        self.assertEqual(len(runner), 1, "task-runner sidecar on the worker")
        self.assertEqual(env_of(runner[0])["N8N_RUNNERS_AUTH_TOKEN"], ("secret", "n8n-core", "N8N_RUNNERS_AUTH_TOKEN"))
        self.assertEqual(env_of(worker["containers"][0])["N8N_RUNNERS_AUTH_TOKEN"],
                         ("secret", "n8n-core", "N8N_RUNNERS_AUTH_TOKEN"))

    def test_render_is_stable(self):
        # A random value (e.g. a generated token) would show up as a change in every diff.
        again = render()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual([d for d in yaml.safe_load_all(again.stdout) if d], self.docs)

    def test_pods_cannot_read_secrets_via_api(self):
        self.assertEqual([d for d in self.docs if d["kind"] in ("Role", "RoleBinding")], [])
        for name in ("flow-test-n8n-main", "flow-test-n8n-worker"):
            spec = self.resource("Deployment", name)["spec"]["template"]["spec"]
            self.assertIs(spec["automountServiceAccountToken"], False)
            self.assertTrue(spec["securityContext"]["runAsNonRoot"])

    def test_no_pdb_blocking_drains(self):
        # A budget over the single main pod would hang every node drain. Upstream renders one only
        # from two main pods on (pdb.yaml); this guards against that changing.
        self.assertEqual([d for d in self.docs if d["kind"] == "PodDisruptionBudget"], [])
        # Two main pods only exist with multi-main, which needs a licence (render only).
        two = render("--set", "n8n.multiMain.enabled=true", "--set", "n8n.license.enabled=true")
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertIn("kind: PodDisruptionBudget", two.stdout, "control: with two main pods there is one")

    def test_network_policies(self):
        app = self.resource("NetworkPolicy", "flow-test-app")["spec"]
        pods = [self.resource("Deployment", n)["spec"]["template"]["metadata"]["labels"]
                for n in ("flow-test-n8n-main", "flow-test-n8n-worker", "flow-test-n8n-webhook-processor")]
        for labels in pods:
            for k, v in app["podSelector"]["matchLabels"].items():
                self.assertEqual(labels.get(k), v, k)
        self.assertEqual(app["policyTypes"], ["Ingress"], "egress stays open for workflows")
        gateway = [r for r in app["ingress"] if "namespaceSelector" in r["from"][0]]
        self.assertEqual(len(gateway), 1)
        self.assertEqual(gateway[0]["from"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"],
                         "example-gateway")
        container_port = self.resource("Deployment", "flow-test-n8n-main")["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"]
        self.assertEqual([p["port"] for p in gateway[0]["ports"]], [container_port])
        db = self.resource("NetworkPolicy", "n8n-db")["spec"]
        self.assertEqual(db["podSelector"]["matchLabels"]["cnpg.io/cluster"], "n8n-db")
        self.assertIn(9187, [p["port"] for r in db["ingress"] for p in r.get("ports", [])])
        self.resource("NetworkPolicy", "n8n-redis")

    def test_default_values_render(self):
        result = render(fixture=False, release="n8n")
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        names = {(d["kind"], d["metadata"]["name"]) for d in docs}
        self.assertIn(("Service", "n8n-main"), names)
        self.assertNotIn("Cluster", {k for k, _ in names})

    def test_mismatches_fail(self):
        cases = [
            (["--set", "n8n.database.host=other"], "n8n.database.host must be n8n-db-rw"),
            (["--set", "n8n.database.passwordSecret.name=other"], "must be n8n-db-app"),
            (["--set", "cnpg.owner=other"], "must match cnpg.owner"),
            (["--set", "n8n.redis.host=other"], "n8n.redis.host must be n8n-redis"),
            (["--set", "redisOperator.existingSecret=other"], "n8n.redis.passwordSecret must be"),
            # The upstream check fires first (placeholder key); ours backs it up.
            (["--set", "n8n.secretRefs.existingSecret="], "secretRefs.existingSecret"),
            (["--set", "n8n.editor.url="], "set n8n.editor.url and n8n.webhook.url"),
            (["--set", "cnpg.image="], "cnpg.image is required"),
            (["--set", "redisOperator.image="], "redisOperator.image is required"),
            (["--set", "networkPolicy.gatewayNamespace="], "networkPolicy.gatewayNamespace is required"),
        ]
        for args, message in cases:
            with self.subTest(args=args):
                result = render(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        result = render("--set", "cnpg.enabled=false", "--set", "n8n.database.host=external-db", fixture=True)
        self.assertNotEqual(result.returncode, 0, "backup without cnpg must fail")
        self.assertIn("cnpg.backup.enabled requires cnpg.enabled", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
