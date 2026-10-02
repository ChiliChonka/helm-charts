#!/usr/bin/env python3
"""Offline Helm render checks; no live cluster or real credentials."""
from pathlib import Path
import subprocess
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1]
RELEASE = "cloud-test"


def render(*args, fixture="ci/test-values.yaml"):
    command = ["helm", "template", RELEASE, str(CHART), "-n", "cloud-test"]
    if fixture:
        command += ["-f", str(CHART / fixture)]
    return subprocess.run(command + list(args), text=True, capture_output=True)


def docs_of(result):
    if result.returncode:
        raise RuntimeError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def find(docs, kind, name=None):
    return [d for d in docs if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]


def pod_of(docs):
    deployments = find(docs, "Deployment")
    assert len(deployments) == 1, deployments
    return deployments[0]["spec"]["template"]["spec"]


def env_of(container):
    return {e["name"]: e for e in container.get("env", [])}


class FixtureTest(unittest.TestCase):
    """The full fictional setup: OIDC, files volume, route."""

    @classmethod
    def setUpClass(cls):
        cls.docs = docs_of(render())
        cls.pod = pod_of(cls.docs)
        cls.main = cls.pod["containers"][0]
        cls.env = env_of(cls.main)

    def test_single_pod_recreate(self):
        spec = find(self.docs, "Deployment")[0]["spec"]
        self.assertEqual(spec["replicas"], 1)
        self.assertEqual(spec["strategy"]["type"], "Recreate")

    def test_route_targets_real_service_port(self):
        routes = find(self.docs, "HTTPRoute")
        self.assertEqual(len(routes), 1)
        route = routes[0]["spec"]
        backend = route["rules"][0]["backendRefs"][0]
        services = find(self.docs, "Service", backend["name"])
        self.assertEqual(len(services), 1, backend)
        port = [p for p in services[0]["spec"]["ports"] if p["port"] == backend["port"]]
        self.assertEqual(len(port), 1)
        self.assertEqual(port[0]["targetPort"], "http")
        self.assertEqual([p["containerPort"] for p in self.main["ports"] if p["name"] == "http"], [9200])
        self.assertEqual(route["parentRefs"][0]["sectionName"], "https")
        self.assertEqual(route["rules"][0]["timeouts"]["request"], "3600s")
        # The service selects the pod.
        labels = find(self.docs, "Deployment")[0]["spec"]["template"]["metadata"]["labels"]
        for key, value in services[0]["spec"]["selector"].items():
            self.assertEqual(labels[key], value)

    def test_volumes_and_claims(self):
        claims = {d["metadata"]["name"]: d for d in find(self.docs, "PersistentVolumeClaim")}
        self.assertEqual(set(claims), {f"{RELEASE}-config", f"{RELEASE}-data", f"{RELEASE}-files"})
        self.assertEqual(claims[f"{RELEASE}-files"]["spec"]["storageClassName"], "example-nfs")
        self.assertEqual(claims[f"{RELEASE}-files"]["spec"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(claims[f"{RELEASE}-data"]["spec"]["resources"]["requests"]["storage"], "50Gi")
        for claim in claims.values():
            self.assertEqual(claim["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        volumes = {v["name"]: v["persistentVolumeClaim"]["claimName"] for v in self.pod["volumes"]
                   if "persistentVolumeClaim" in v}
        self.assertEqual(volumes, {"config": f"{RELEASE}-config", "data": f"{RELEASE}-data",
                                   "files": f"{RELEASE}-files"})
        mounts = {m["name"]: m["mountPath"] for m in self.main["volumeMounts"]}
        self.assertEqual(mounts, {"config": "/etc/ocis", "data": "/var/lib/ocis",
                                  "files": "/var/lib/ocis-files", "csp": "/etc/ocis-csp"})
        self.assertEqual(self.env["STORAGE_USERS_OCIS_ROOT"]["value"], mounts["files"])

    def test_init_writes_config_once_and_hides_output(self):
        init = self.pod["initContainers"]
        self.assertEqual(len(init), 1)
        script = init[0]["command"][-1]
        self.assertIn("[ -f /etc/ocis/ocis.yaml ]", script)
        self.assertIn("ocis init --insecure false >/dev/null", script)
        self.assertEqual(init[0]["image"], self.main["image"])
        self.assertEqual({m["mountPath"] for m in init[0]["volumeMounts"]}, {"/etc/ocis"})

    def test_oidc_settings(self):
        expected = {
            "OCIS_URL": "https://cloud.example.com",
            "OCIS_OIDC_ISSUER": "https://sso.example.com/realms/example",
            "OCIS_EXCLUDE_RUN_SERVICES": "idp",
            "WEB_OIDC_CLIENT_ID": "web",
            "PROXY_OIDC_REWRITE_WELLKNOWN": "true",
            "PROXY_USER_OIDC_CLAIM": "preferred_username",
            "PROXY_USER_CS3_CLAIM": "username",
            "PROXY_AUTOPROVISION_ACCOUNTS": "true",
            "PROXY_ROLE_ASSIGNMENT_DRIVER": "oidc",
            "PROXY_ROLE_ASSIGNMENT_OIDC_CLAIM": "roles",
            "GRAPH_ASSIGN_DEFAULT_USER_ROLE": "false",
            "PROXY_TLS": "false",
            "OCIS_DEFAULT_LANGUAGE": "de",
        }
        for name, value in expected.items():
            self.assertEqual(self.env[name]["value"], value, name)
        self.assertNotIn("WEB_OIDC_SCOPE", self.env)
        self.assertNotIn("IDM_ADMIN_PASSWORD", self.env)

    def test_secrets_only_by_reference(self):
        self.assertEqual(find(self.docs, "Secret"), [])
        self.assertEqual(self.env["NOTIFICATIONS_SMTP_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "example-smtp", "key": "password"})

    def test_runs_unprivileged(self):
        self.assertEqual(self.pod["securityContext"]["runAsUser"], 1000)
        self.assertEqual(self.pod["securityContext"]["fsGroup"], 1000)
        for c in self.pod["containers"] + self.pod["initContainers"]:
            self.assertFalse(c["securityContext"]["allowPrivilegeEscalation"])

    def test_host_alias_for_the_public_name(self):
        self.assertEqual(self.pod["hostAliases"], [{"ip": "192.0.2.41", "hostnames": ["cloud.example.com"]}])

    def test_image_matches_app_version(self):
        version = yaml.safe_load((CHART / "Chart.yaml").read_text())["appVersion"]
        self.assertEqual(self.main["image"], f"docker.io/owncloud/ocis:{version}")


# oCIS 8.2.1 built-in policy (services/proxy/pkg/config/csp.yaml). The chart's file REPLACES it.
OCIS_DEFAULT_CONNECT_SRC = ["'self'", "blob:", "https://raw.githubusercontent.com/owncloud/awesome-ocis/"]


def csp_of(docs):
    maps = [d for d in find(docs, "ConfigMap") if d["metadata"]["name"].endswith("-csp")]
    assert len(maps) == 1, maps
    return yaml.safe_load(maps[0]["data"]["csp.yaml"])["directives"]


class CspTest(unittest.TestCase):
    """Regression 2026-10-03: without the IDP in connect-src the browser never reaches Keycloak
    ("problems connecting to the login service"); token tests with curl do not see a CSP."""

    def test_issuer_origin_allowed_for_the_browser(self):
        docs = docs_of(render())
        connect = csp_of(docs)["connect-src"]
        self.assertEqual(connect, OCIS_DEFAULT_CONNECT_SRC + ["https://sso.example.com/"])

    def test_policy_is_mounted_where_ocis_reads_it(self):
        docs = docs_of(render())
        pod = pod_of(docs)
        main = pod["containers"][0]
        location = env_of(main)["PROXY_CSP_CONFIG_FILE_LOCATION"]["value"]
        mount = [m for m in main["volumeMounts"] if location.startswith(m["mountPath"] + "/")]
        self.assertEqual(len(mount), 1, location)
        volume = [v for v in pod["volumes"] if v["name"] == mount[0]["name"]][0]
        self.assertEqual(volume["configMap"]["name"], f"{RELEASE}-csp")
        self.assertEqual(location.rsplit("/", 1)[1], "csp.yaml")

    def test_without_oidc_the_defaults_stay(self):
        docs = docs_of(render(fixture="ci/required-values.yaml"))
        directives = csp_of(docs)
        self.assertEqual(directives["connect-src"], OCIS_DEFAULT_CONNECT_SRC)
        self.assertEqual(directives["default-src"], ["'none'"])
        self.assertEqual(len(directives), 12)

    def test_issuer_with_port_and_path_gives_origin_only(self):
        docs = docs_of(render("--set", "oidc.issuer=https://sso.example.com:8443/realms/x"))
        self.assertIn("https://sso.example.com:8443/", csp_of(docs)["connect-src"])

    def test_policy_change_restarts_the_pod(self):
        a = pod_of(docs_of(render()))
        b = pod_of(docs_of(render("--set", "oidc.issuer=https://other.example.com/realms/x")))
        self.assertNotEqual(a, b)
        key = "checksum/csp"
        ann = lambda docs: find(docs, "Deployment")[0]["spec"]["template"]["metadata"]["annotations"][key]
        self.assertNotEqual(ann(docs_of(render())),
                            ann(docs_of(render("--set", "oidc.issuer=https://other.example.com/realms/x"))))


class BuiltinLoginTest(unittest.TestCase):
    """Required values only: built-in IDP, admin password from a Secret, no files volume."""

    @classmethod
    def setUpClass(cls):
        cls.docs = docs_of(render(fixture="ci/required-values.yaml"))
        cls.pod = pod_of(cls.docs)

    def test_admin_password_from_secret_in_init_and_server(self):
        ref = {"name": "example-ocis-admin", "key": "password"}
        for container in (self.pod["initContainers"][0], self.pod["containers"][0]):
            env = env_of(container)
            self.assertEqual(env["IDM_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"], ref)

    def test_no_oidc_no_files(self):
        env = env_of(self.pod["containers"][0])
        self.assertEqual(set(env), {"OCIS_URL", "OCIS_LOG_LEVEL", "OCIS_INSECURE", "PROXY_TLS",
                                    "PROXY_HTTP_ADDR", "PROXY_CSP_CONFIG_FILE_LOCATION",
                                    "IDM_ADMIN_PASSWORD"})
        for name in ("OCIS_EXCLUDE_RUN_SERVICES", "OCIS_OIDC_ISSUER", "STORAGE_USERS_OCIS_ROOT"):
            self.assertNotIn(name, env)
        self.assertEqual(len(find(self.docs, "PersistentVolumeClaim")), 2)
        self.assertEqual(find(self.docs, "HTTPRoute"), [])
        self.assertNotIn("hostAliases", self.pod)

    def test_existing_claims_are_used_not_created(self):
        docs = docs_of(render("--set", "persistence.data.existingClaim=old-data",
                              fixture="ci/required-values.yaml"))
        names = [d["metadata"]["name"] for d in find(docs, "PersistentVolumeClaim")]
        self.assertEqual(names, [f"{RELEASE}-config"])
        volumes = {v["name"]: v["persistentVolumeClaim"]["claimName"] for v in pod_of(docs)["volumes"]
                   if "persistentVolumeClaim" in v}
        self.assertEqual(volumes["data"], "old-data")

    def test_role_driver_default_keeps_default_role(self):
        docs = docs_of(render("--set", "oidc.roleAssignment.driver=default"))
        env = env_of(pod_of(docs)["containers"][0])
        self.assertEqual(env["PROXY_ROLE_ASSIGNMENT_DRIVER"]["value"], "default")
        self.assertNotIn("GRAPH_ASSIGN_DEFAULT_USER_ROLE", env)
        self.assertNotIn("PROXY_ROLE_ASSIGNMENT_OIDC_CLAIM", env)


class RejectTest(unittest.TestCase):
    """Combinations that must fail, each with its own message (not just any failure)."""

    def assertFails(self, message, *args, fixture="ci/test-values.yaml"):
        result = render(*args, fixture=fixture)
        self.assertNotEqual(result.returncode, 0, result.stdout[:200])
        self.assertIn(message, result.stderr)

    def test_defaults_alone(self):
        self.assertFails("url is required", fixture=None)

    def test_url(self):
        self.assertFails("must start with https://", "--set", "url=http://cloud.example.com")
        self.assertFails("must not end with a slash", "--set", "url=https://cloud.example.com/")

    def test_builtin_login_needs_admin_secret(self):
        self.assertFails("admin.existingSecret is required", "--set", "oidc.enabled=false")

    def test_oidc_needs_issuer_and_known_driver(self):
        self.assertFails("oidc.issuer is required", "--set", "oidc.issuer=")
        self.assertFails("driver must be oidc or default", "--set", "oidc.roleAssignment.driver=ldap")

    def test_plain_secret_in_extra_env(self):
        self.assertFails("must use a Secret reference",
                         "--set", "extraEnv[0].name=NOTIFICATIONS_SMTP_PASSWORD",
                         "--set", "extraEnv[0].value=hunter2")

    def test_route_without_entries(self):
        self.assertFails("at least one entry", "--set", "httpRoute.routes=null")


if __name__ == "__main__":
    unittest.main(verbosity=1)
