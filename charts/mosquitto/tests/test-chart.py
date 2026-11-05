#!/usr/bin/env python3
"""Offline Helm render checks; no live cluster or real credentials."""
from pathlib import Path
import subprocess
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1]


def render(*args, release="mosquitto", fixture="ci/test-values.yaml"):
    command = ["helm", "template", release, str(CHART), "-n", "mqtt"]
    if fixture:
        command += ["-f", str(CHART / fixture)]
    result = subprocess.run(command + list(args), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def find(docs, kind):
    return [d for d in docs if d["kind"] == kind]


class Migration(unittest.TestCase):
    """Names and selectors match the t3n/mosquitto 2.4.1 release this chart replaces."""

    @classmethod
    def setUpClass(cls):
        cls.docs = render()
        cls.dep = find(cls.docs, "Deployment")[0]

    def test_object_names(self):
        for kind in ("Deployment", "Service", "ConfigMap", "ServiceAccount", "PersistentVolumeClaim"):
            self.assertEqual([d["metadata"]["name"] for d in find(self.docs, kind)], ["mosquitto"], kind)

    def test_selector_stays_immutable(self):
        want = {"app.kubernetes.io/name": "mosquitto", "app.kubernetes.io/instance": "mosquitto"}
        self.assertEqual(self.dep["spec"]["selector"]["matchLabels"], want)
        self.assertEqual(find(self.docs, "Service")[0]["spec"]["selector"], want)
        self.assertEqual(self.dep["spec"]["template"]["metadata"]["labels"], want)

    def test_container_and_data_path(self):
        pod = self.dep["spec"]["template"]["spec"]
        self.assertEqual(pod["containers"][0]["name"], "mosquitto")
        data = [m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "data"]
        self.assertEqual(data, [{"name": "data", "mountPath": "/mosquitto/data"}])
        claim = [v for v in pod["volumes"] if v["name"] == "data"][0]
        self.assertEqual(claim["persistentVolumeClaim"]["claimName"], "mosquitto")

    def test_recreate_for_rwo_volume(self):
        self.assertEqual(self.dep["spec"]["strategy"]["type"], "Recreate")


class Probes(unittest.TestCase):
    def test_default_is_not_every_second(self):
        main = find(render(), "Deployment")[0]["spec"]["template"]["spec"]["containers"][0]
        self.assertGreaterEqual(main["livenessProbe"]["periodSeconds"], 15)
        self.assertEqual(main["readinessProbe"]["periodSeconds"], 5)

    def test_values_reach_the_pod(self):
        main = find(render("--set", "livenessProbe.periodSeconds=77"), "Deployment")[0]
        probe = main["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]
        self.assertEqual(probe["periodSeconds"], 77)


class Auth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pod = find(render(), "Deployment")[0]["spec"]["template"]["spec"]

    def test_init_copies_without_root(self):
        init = self.pod["initContainers"][0]
        self.assertEqual(init["securityContext"]["runAsUser"], 1883)
        self.assertIs(init["securityContext"]["runAsNonRoot"], True)
        self.assertEqual(init["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertEqual(self.pod["securityContext"]["fsGroup"], 1883)

    def test_secret_stays_out_of_the_broker(self):
        main = self.pod["containers"][0]
        self.assertNotIn("auth-secret", [m["name"] for m in main["volumeMounts"]])
        auth = [m for m in main["volumeMounts"] if m["name"] == "auth"]
        self.assertEqual(auth, [{"name": "auth", "mountPath": "/etc/mosquitto/auth", "readOnly": True}])

    def test_secret_is_group_readable_only(self):
        vol = [v for v in self.pod["volumes"] if v["name"] == "auth-secret"][0]["secret"]
        self.assertEqual(vol["secretName"], "example-mosquitto-auth")
        self.assertEqual(vol["defaultMode"], 0o440)

    def test_no_credentials_in_the_chart(self):
        self.assertEqual(find(render(), "Secret"), [])

    def test_without_secret_no_init(self):
        pod = find(render(fixture=None), "Deployment")[0]["spec"]["template"]["spec"]
        self.assertNotIn("initContainers", pod)
        self.assertNotIn("auth", [v["name"] for v in pod["volumes"]])


class Acl(unittest.TestCase):
    def test_copied_next_to_the_password_file(self):
        pod = find(render(), "Deployment")[0]["spec"]["template"]["spec"]
        init = pod["initContainers"][0]
        self.assertIn("cp /acl/aclfile /auth/aclfile", init["args"][0])
        self.assertIn("chmod 0400 /auth/*", init["args"][0])
        self.assertIn({"name": "config", "mountPath": "/acl", "readOnly": True}, init["volumeMounts"])

    def test_content_in_the_configmap(self):
        data = find(render(), "ConfigMap")[0]["data"]
        self.assertIn("pattern readwrite %u/#\n", data["aclfile"])

    def test_works_without_a_secret(self):
        pod = find(render("--set", "auth.existingSecret="), "Deployment")[0]["spec"]["template"]["spec"]
        init = pod["initContainers"][0]
        self.assertNotIn("cp /secret", init["args"][0])
        self.assertNotIn("auth-secret", [m["name"] for m in init["volumeMounts"]])
        self.assertIn("auth", [v["name"] for v in pod["volumes"]])

    def test_no_acl_no_file(self):
        data = find(render("--set", "acl="), "ConfigMap")[0]["data"]
        self.assertNotIn("aclfile", data)
        init = find(render("--set", "acl="), "Deployment")[0]["spec"]["template"]["spec"]["initContainers"][0]
        self.assertNotIn("/acl", init["args"][0])


class Config(unittest.TestCase):
    def test_config_arrives_verbatim(self):
        text = find(render(), "ConfigMap")[0]["data"]["mosquitto.conf"]
        self.assertIn("password_file /etc/mosquitto/auth/passwordfile\n", text)
        self.assertIn("allow_anonymous false\n", text)

    def test_default_refuses_anonymous(self):
        text = find(render(fixture=None), "ConfigMap")[0]["data"]["mosquitto.conf"]
        self.assertIn("allow_anonymous false\n", text)

    def test_config_change_restarts_the_pod(self):
        one = find(render(), "Deployment")[0]
        two = find(render("--set-string", "config=listener 1883"), "Deployment")[0]
        key = "checksum/config"
        self.assertNotEqual(one["spec"]["template"]["metadata"]["annotations"][key],
                            two["spec"]["template"]["metadata"]["annotations"][key])


class Tls(unittest.TestCase):
    """Key and certificate are copied like the password file: no symlink, owned by the broker."""

    @classmethod
    def setUpClass(cls):
        cls.docs = render()
        cls.pod = find(cls.docs, "Deployment")[0]["spec"]["template"]["spec"]

    def test_init_copies_key_and_cert(self):
        init = self.pod["initContainers"][0]
        self.assertIn("cp /tls-secret/tls.crt /tls-secret/tls.key /tls/", init["args"][0])
        self.assertIn("chmod 0400 /tls/*", init["args"][0])
        self.assertIn({"name": "tls-secret", "mountPath": "/tls-secret", "readOnly": True}, init["volumeMounts"])
        self.assertIn({"name": "tls", "mountPath": "/tls"}, init["volumeMounts"])

    def test_secret_stays_out_of_the_broker(self):
        main = self.pod["containers"][0]
        self.assertNotIn("tls-secret", [m["name"] for m in main["volumeMounts"]])
        tls = [m for m in main["volumeMounts"] if m["name"] == "tls"]
        self.assertEqual(tls, [{"name": "tls", "mountPath": "/etc/mosquitto/tls", "readOnly": True}])
        vols = {v["name"]: v for v in self.pod["volumes"]}
        self.assertEqual(vols["tls"]["emptyDir"], {"medium": "Memory", "sizeLimit": "1Mi"})
        secret = vols["tls-secret"]["secret"]
        self.assertEqual(secret["secretName"], "example-mosquitto-tls")
        self.assertEqual(secret["defaultMode"], 0o440)
        self.assertEqual([i["key"] for i in secret["items"]], ["tls.crt", "tls.key"])

    def test_port_on_pod_and_service(self):
        main = self.pod["containers"][0]
        self.assertIn({"name": "mqtts", "containerPort": 8883, "protocol": "TCP"}, main["ports"])
        ports = find(self.docs, "Service")[0]["spec"]["ports"]
        self.assertIn({"name": "mqtts", "port": 8883, "targetPort": "mqtts", "protocol": "TCP"}, ports)

    def test_without_secret_nothing(self):
        pod = find(render("--set", "tls.existingSecret="), "Deployment")[0]["spec"]["template"]["spec"]
        self.assertNotIn("/tls", pod["initContainers"][0]["args"][0])
        names = [v["name"] for v in pod["volumes"]]
        self.assertNotIn("tls", names)
        self.assertNotIn("tls-secret", names)

    def test_tls_alone_still_gets_the_init_copy(self):
        pod = find(render("--set", "auth.existingSecret=", "--set", "acl="), "Deployment")[0]["spec"]["template"]["spec"]
        init = pod["initContainers"][0]
        self.assertNotIn("/auth", init["args"][0])
        self.assertNotIn("auth", [m["name"] for m in init["volumeMounts"]])
        self.assertIn("cp /tls-secret/tls.crt", init["args"][0])


if __name__ == "__main__":
    unittest.main()
