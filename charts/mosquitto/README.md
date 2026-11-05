# Mosquitto

Own chart for [Eclipse Mosquitto](https://mosquitto.org) **2.1.2**: one broker, a persistent
volume, passwords from an existing Secret and probes you can set. It replaces `t3n/mosquitto`
2.4.1 in place: object names, selectors and the volume stay the same, so `helm upgrade` of a
release named `mosquitto` keeps the data.

## Install

```sh
helm template mosquitto charts/mosquitto -n mosquitto -f /path/to/private-values.yaml
helm upgrade --install mosquitto charts/mosquitto -n mosquitto --create-namespace -f /path/to/private-values.yaml
```

Defaults render a broker that refuses everyone: `allow_anonymous false` and no password file.
[ci/test-values.yaml](ci/test-values.yaml) shows a fictional setup with passwords.

## Things that bite

- **Passwords:** `config` is written as is. With `auth.existingSecret`, name the file in the
  config yourself: `password_file /etc/mosquitto/auth/passwordfile`.
- **Why an init container:** Mosquitto 2.1 refuses a symlink, which is what a Secret mount is, and
  warns that future versions will refuse a file not owned by `mosquitto`, which is what a Secret
  mount with `subPath` is. The init container copies the file into a memory-backed `emptyDir` as
  uid 1883, with no capabilities. It reads the `0440` Secret through `fsGroup: 1883`; leave
  that set.
- **ACLs:** `acl` holds the file, and `config` must name it: `acl_file /etc/mosquitto/auth/aclfile`.
  A pattern rule such as `pattern readwrite %u/#` applies to every user. Publishing a denied topic
  fails silently for MQTT 3.1.1 clients, so test each client, not just the connection.
- **TLS:** `tls.existingSecret` takes a `kubernetes.io/tls` Secret (cert-manager writes one). Key
  and certificate are copied like the password file, to `/etc/mosquitto/tls`. Name them in
  `config` right after their listener (`listener 8883 0.0.0.0`, `certfile …/tls.crt`,
  `keyfile …/tls.key`) and add the port: `ports.mqtts: 8883`. Tested against a real broker: TLS
  login works, plain MQTT on that port fails with a protocol error. A renewed certificate, like a
  changed password file, needs a pod restart.
- **Changed Secret:** the copy happens at pod start, and the broker reads the file only then.
  Restart the pod after a change.
- **Probes and the log:** each TCP probe is a connection and two log lines. The old chart probed
  every second and every three seconds, about 230,000 lines a day; the defaults here make about
  46,000. A long readiness period saves little and costs a lot: the Service gets the pod only after
  the first success, so 30 s meant 35 s of downtime on a restart with a broker up after 3 s.
- **Websocket port:** the default probes use port `websocket`. If you remove that listener from
  `config`, point the probes at `mqtt`.
