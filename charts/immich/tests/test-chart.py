#!/usr/bin/env python3
"""Offline Helm integration checks; no live cluster or real credentials."""
from pathlib import Path
import subprocess
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1]


def render(*args, fixture=True):
    command = ["helm", "template", "photo-test", str(CHART), "-n", "photo-test"]
    if fixture:
        command += ["-f", str(CHART / "ci/test-values.yaml")]
    return subprocess.run(command + list(args), text=True, capture_output=True)


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
        self.assertEqual(len(matches), 1, (kind, name))
        return matches[0]

    def test_route_targets_real_service_and_root(self):
        route = self.resource("HTTPRoute")["spec"]
        backend = route["rules"][0]["backendRefs"][0]
        service = self.resource("Service", backend["name"])
        self.assertIn(backend["port"], [p["port"] for p in service["spec"]["ports"]])
        self.assertEqual(route["parentRefs"][0]["sectionName"], "https")
        self.assertEqual(route["rules"][0]["matches"][0]["path"]["value"], "/")
        self.assertEqual(route["rules"][0]["timeouts"]["request"], "3600s")

    def test_database_and_credentials_wiring(self):
        cluster = self.resource("Cluster")["spec"]
        self.assertFalse(cluster["enableSuperuserAccess"])
        self.assertEqual(cluster["primaryUpdateMethod"], "switchover")
        self.assertEqual(cluster["storage"]["storageClass"], "example-block")
        self.assertIn("vchord.so", cluster["postgresql"]["shared_preload_libraries"])
        sql = cluster["bootstrap"]["initdb"]["postInitApplicationSQL"]
        self.assertTrue(any("vchord CASCADE" in s for s in sql))
        self.assertTrue(any("earthdistance CASCADE" in s for s in sql))
        server = self.resource("Deployment", "photo-test-server")["spec"]
        self.assertEqual(server["strategy"]["type"], "Recreate")
        env = {e["name"]: e for e in server["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["DB_HOSTNAME"]["value"], "immich-db-rw")
        self.assertEqual(env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"], "immich-db-app")
        redis = self.resource("Redis")["spec"]
        self.assertEqual(env["REDIS_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         redis["kubernetesConfig"]["redisSecret"])
        self.assertEqual(env["REDIS_HOSTNAME"]["value"], self.resource("Redis")["metadata"]["name"])
        self.assertEqual(cluster["plugins"][0]["parameters"]["barmanObjectName"],
                         self.resource("ObjectStore")["metadata"]["name"])
        self.assertEqual(self.resource("ScheduledBackup")["spec"]["cluster"]["name"],
                         self.resource("Cluster")["metadata"]["name"])
        s3 = self.resource("ObjectStore")["spec"]["configuration"]["s3Credentials"]
        self.assertEqual(s3["accessKeyId"], {"name": "example-s3-credentials", "key": "access-key-id"})
        self.assertEqual(s3["region"], {"name": "example-s3-credentials", "key": "region"})

    def test_external_config_and_credentials_stay_out_of_ml(self):
        server = self.resource("Deployment", "photo-test-server")["spec"]["template"]["spec"]
        volumes = {v["name"]: v for v in server["volumes"]}
        self.assertEqual(volumes["config"]["secret"]["secretName"], "example-immich-config")
        self.assertEqual(volumes["data"]["persistentVolumeClaim"]["claimName"], "immich-library")
        self.assertFalse(any(d["kind"] == "Secret" for d in self.docs))
        self.assertFalse(any(d["kind"] == "ConfigMap" and "immich-config" in d["metadata"]["name"]
                             for d in self.docs))
        ml = self.resource("Deployment", "photo-test-machine-learning")["spec"]["template"]["spec"]
        for env in ml["containers"][0]["env"]:
            self.assertNotIn(env["name"], ["DB_PASSWORD", "DB_USERNAME", "REDIS_PASSWORD"])
        self.assertEqual({v["name"] for v in ml["volumes"]}, {"cache"})
        version = yaml.safe_load((CHART / "Chart.yaml").read_text())["appVersion"]
        for pod in [server, ml]:
            self.assertTrue(pod["containers"][0]["image"].endswith(":" + version))

    def test_persistence(self):
        claim = self.resource("PersistentVolumeClaim", "immich-library")
        self.assertEqual(claim["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        self.assertEqual(claim["spec"]["storageClassName"], "example-nfs")
        ml = self.resource("PersistentVolumeClaim", "photo-test-machine-learning")
        self.assertEqual(ml["spec"]["resources"]["requests"]["storage"], "10Gi")
        # Persistent Redis needs fsGroup: the image runs as uid 1000, a block volume is root-owned.
        self.assertEqual(self.resource("Redis")["spec"]["podSecurityContext"],
                         {"runAsUser": 1000, "fsGroup": 1000})
        redis = self.resource("Redis")["spec"]["storage"]
        self.assertTrue(redis["keepAfterDelete"])
        self.assertEqual(redis["volumeClaimTemplate"]["spec"]["resources"]["requests"]["storage"], "1Gi")
        conf = self.resource("ConfigMap", "immich-redis-config")["data"]["redis-config"]
        self.assertIn("appendonly yes", conf)
        self.assertIn("maxmemory-policy noeviction", conf)

    def test_defaults_do_not_create_platform_resources(self):
        result = render(fixture=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        self.assertFalse({"Cluster", "Redis", "HTTPRoute", "ObjectStore", "Secret"}
                         & {d["kind"] for d in docs})
        self.assertFalse(any(d["kind"] == "ConfigMap" for d in docs))

    def test_cnpg_requires_disabling_native_dumps(self):
        result = render("--set", "cnpg.enabled=true", fixture=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CNPG requires", result.stderr)
        result = render("--set", "cnpg.enabled=true",
                        "--set", "immich.immich.configuration.backup.database.enabled=false",
                        fixture=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        cfg = next(d for d in docs if d["kind"] == "ConfigMap")
        self.assertFalse(yaml.safe_load(cfg["data"]["immich-config.yaml"])["backup"]["database"]["enabled"])

    def test_ui_managed_settings(self):
        # No config file at all: nothing may be rendered or mounted for it.
        result = render("--set", "cnpg.enabled=true", "--set", "uiManagedSettings=true", fixture=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        self.assertEqual(len([d for d in docs if d["kind"] == "Cluster"]), 1)
        self.assertFalse(any(d["kind"] == "ConfigMap" for d in docs))
        server = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-server"))
        pod = server["spec"]["template"]["spec"]
        self.assertNotIn("config", {v["name"] for v in pod["volumes"]})
        self.assertNotIn("IMMICH_CONFIG_FILE", {e["name"] for e in pod["containers"][0]["env"]})
        # ... and it excludes a config file, which would lock the UI.
        result = render("--set", "uiManagedSettings=true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exclude each other", result.stderr)

    def test_region_is_optional(self):
        result = render("--set", "cnpg.backup.regionKey=")
        self.assertEqual(result.returncode, 0, result.stderr)
        store = next(d for d in yaml.safe_load_all(result.stdout) if d and d["kind"] == "ObjectStore")
        self.assertNotIn("region", store["spec"]["configuration"]["s3Credentials"])

    def test_default_omits_primary_update_method(self):
        result = render("--set", "cnpg.enabled=true", "--set", "uiManagedSettings=true", fixture=False)
        cluster = next(d for d in yaml.safe_load_all(result.stdout) if d and d["kind"] == "Cluster")
        self.assertNotIn("primaryUpdateMethod", cluster["spec"])

    def test_bundled_valkey_needs_its_own_hostname(self):
        result = render("--set", "immich.valkey.enabled=true", fixture=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REDIS_HOSTNAME", result.stderr)
        result = render("--set", "immich.valkey.enabled=true", "--set",
                        "immich.server.controllers.main.containers.main.env.REDIS_HOSTNAME=photo-test-valkey",
                        fixture=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_combinations_fail(self):
        cases = [
            ("immich.valkey.enabled=true", "Choose redisOperator"),
            ("cnpg.enabled=false", "requires cnpg.enabled"),
            ("redisOperator.existingSecret=", "existingSecret is required"),
            ("httpRoute.parentRef.sectionName=", "sectionName is required"),
            ("immich.immich.configurationKind=ConfigMap", "configurationKind: Secret"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                result = render("--set", value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_inline_credentials_fail(self):
        cases = [
            "immich.immich.configuration.oauth.clientSecret",
            "immich.server.controllers.main.containers.main.env.DB_PASSWORD",
            "immich.server.controllers.main.containers.main.env.REDIS_PASSWORD",
        ]
        for key in cases:
            with self.subTest(key=key):
                result = render("--set", key + "=fixture-only", fixture=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("never values", result.stderr)


if __name__ == "__main__":
    unittest.main()
