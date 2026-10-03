#!/usr/bin/env python3
"""Offline Helm render checks; no live cluster or real credentials."""
from pathlib import Path
import json
import subprocess
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1]
RELEASE = "dav-test"


def render(*args, fixture="ci/test-values.yaml"):
    command = ["helm", "template", RELEASE, str(CHART), "-n", "dav-test"]
    if fixture:
        command += ["-f", str(CHART / fixture)]
    return subprocess.run(command + list(args), text=True, capture_output=True)


def docs_of(result):
    if result.returncode:
        raise RuntimeError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def find(docs, kind, name=None):
    return [d for d in docs if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]


def env_of(container):
    return {e["name"]: e for e in container.get("env", [])}


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = docs_of(render())
        dep = find(cls.docs, "Deployment")
        assert len(dep) == 1
        cls.dep = dep[0]["spec"]
        cls.pod = cls.dep["template"]["spec"]
        cls.main = cls.pod["containers"][0]
        cls.env = env_of(cls.main)

    def test_single_pod_without_service_links(self):
        self.assertEqual(self.dep["replicas"], 1)
        self.assertEqual(self.dep["strategy"]["type"], "Recreate")
        # A Service named like the app would inject RUSTICAL_* variables RustiCal rejects.
        self.assertIs(self.pod["enableServiceLinks"], False)

    def test_listens_on_ipv4_port_of_service_and_route(self):
        self.assertEqual(self.env["RUSTICAL_HTTP__BIND"]["value"], "0.0.0.0:4000")
        self.assertNotIn("RUSTICAL_HTTP__HOST", self.env)
        route = find(self.docs, "HTTPRoute")[0]["spec"]
        backend = route["rules"][0]["backendRefs"][0]
        service = find(self.docs, "Service", backend["name"])[0]["spec"]
        port = [p for p in service["ports"] if p["port"] == backend["port"]]
        self.assertEqual(len(port), 1)
        self.assertEqual([p["containerPort"] for p in self.main["ports"] if p["name"] == port[0]["targetPort"]],
                         [int(self.env["RUSTICAL_HTTP__BIND"]["value"].rsplit(":", 1)[1])])
        self.assertEqual(self.main["readinessProbe"]["httpGet"]["path"], "/ping")

    def test_oidc_environment_as_rustical_reads_it(self):
        # Values as verified against RustiCal 0.16.4 with Keycloak (local cluster, 2026-10-03).
        self.assertEqual(self.env["RUSTICAL_OIDC__ISSUER"]["value"], "https://sso.example.com/realms/example")
        self.assertEqual(self.env["RUSTICAL_OIDC__CLIENT_SECRET"]["valueFrom"]["secretKeyRef"],
                         {"name": "example-rustical-oidc", "key": "client-secret"})
        self.assertEqual(json.loads(self.env["RUSTICAL_OIDC__SCOPES"]["value"]), ["openid", "profile"])
        self.assertEqual(self.env["RUSTICAL_OIDC__REQUIRE_GROUP"]["value"], "family")
        self.assertEqual(json.loads(self.env["RUSTICAL_OIDC__ASSIGN_MEMBERSHIPS__family"]["value"]), ["family"])
        self.assertEqual(self.env["RUSTICAL_FRONTEND__ALLOW_PASSWORD_LOGIN"]["value"], "false")
        self.assertEqual(find(self.docs, "Secret"), [])

    def test_runs_unprivileged_read_only(self):
        self.assertEqual(self.pod["securityContext"]["runAsUser"], 1000)
        self.assertTrue(self.main["securityContext"]["readOnlyRootFilesystem"])
        mounts = {m["mountPath"]: m["name"] for m in self.main["volumeMounts"]}
        self.assertEqual(mounts, {"/var/lib/rustical": "data"})

    def test_backup_job_wiring(self):
        claims = {d["metadata"]["name"]: d["spec"] for d in find(self.docs, "PersistentVolumeClaim")}
        self.assertEqual(claims[f"{RELEASE}-data"]["storageClassName"], "example-block")
        self.assertEqual(claims[f"{RELEASE}-backup"]["storageClassName"], "example-nfs")
        cron = find(self.docs, "CronJob")[0]["spec"]
        self.assertEqual(cron["concurrencyPolicy"], "Forbid")
        pod = cron["jobTemplate"]["spec"]["template"]["spec"]
        c = pod["containers"][0]
        volumes = {v["name"]: v for v in pod["volumes"]}
        mounts = {m["mountPath"]: m["name"] for m in c["volumeMounts"]}
        env = env_of(c)
        # DB path = where the app keeps it, on the app's claim
        self.assertEqual(env["DB"]["value"], "/data/db.sqlite3")
        self.assertEqual(volumes[mounts["/data"]]["persistentVolumeClaim"]["claimName"], f"{RELEASE}-data")
        self.assertEqual(volumes[mounts[env["OUT"]["value"]]]["persistentVolumeClaim"]["claimName"],
                         f"{RELEASE}-backup")
        self.assertEqual(c["command"], ["sh", "/scripts/backup.sh"])
        script = find(self.docs, "ConfigMap", f"{RELEASE}-backup")[0]["data"]["backup.sh"]
        self.assertEqual(script, (CHART / "files/backup.sh").read_text())
        # same node as the app (ReadWriteOnce)
        term = pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
        app_labels = find(self.docs, "Deployment")[0]["spec"]["template"]["metadata"]["labels"]
        for k, v in term["labelSelector"]["matchLabels"].items():
            self.assertEqual(app_labels[k], v)
        self.assertEqual(c["image"], "docker.io/alpine/sqlite:3.53.4")

    def test_image_matches_app_version(self):
        version = yaml.safe_load((CHART / "Chart.yaml").read_text())["appVersion"]
        self.assertEqual(self.main["image"], f"ghcr.io/lennart-k/rustical:{version}")


class DefaultsTest(unittest.TestCase):
    def test_defaults_password_login_no_backup_no_route(self):
        docs = docs_of(render(fixture=None))
        env = env_of(find(docs, "Deployment")[0]["spec"]["template"]["spec"]["containers"][0])
        self.assertEqual(env["RUSTICAL_FRONTEND__ALLOW_PASSWORD_LOGIN"]["value"], "true")
        self.assertFalse(any(k.startswith("RUSTICAL_OIDC") for k in env))
        self.assertEqual(find(docs, "CronJob"), [])
        self.assertEqual(find(docs, "HTTPRoute"), [])
        self.assertEqual(len(find(docs, "PersistentVolumeClaim")), 1)
        self.assertNotIn("hostAliases", find(docs, "Deployment")[0]["spec"]["template"]["spec"])

    def test_host_aliases_passed_through(self):
        docs = docs_of(render("--set", "hostAliases[0].ip=192.0.2.1", "--set", "hostAliases[0].hostnames[0]=sso.example.com",
                              fixture=None))
        self.assertEqual(find(docs, "Deployment")[0]["spec"]["template"]["spec"]["hostAliases"],
                         [{"ip": "192.0.2.1", "hostnames": ["sso.example.com"]}])


class RejectTest(unittest.TestCase):
    def assertFails(self, message, *args):
        result = render(*args)
        self.assertNotEqual(result.returncode, 0, result.stdout[:200])
        self.assertIn(message, result.stderr)

    def test_oidc_needs_issuer_and_secret(self):
        self.assertFails("oidc.issuer is required", "--set", "oidc.issuer=")
        self.assertFails("oidc.existingSecret is required", "--set", "oidc.existingSecret=")

    def test_claim_and_membership_keys(self):
        self.assertFails("claimUserid must be", "--set", "oidc.claimUserid=name")
        self.assertFails("only letters, digits", "--set-json", 'oidc.assignMemberships={"a b":["x"]}')
        self.assertFails("must be a list", "--set-json", 'oidc.assignMemberships={"family":"family"}')

    def test_plain_secret_in_extra_env(self):
        self.assertFails("must use a Secret reference", "--set", "extraEnv[0].name=RUSTICAL_X_TOKEN",
                         "--set", "extraEnv[0].value=x")


if __name__ == "__main__":
    unittest.main(verbosity=1)
