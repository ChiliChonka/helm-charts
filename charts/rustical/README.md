# RustiCal

Own chart for [RustiCal](https://github.com/lennart-k/rustical) **0.16.4**, a CalDAV/CardDAV
server written in Rust: one binary, data in one SQLite database. Login through an OpenID Connect
provider, app tokens for calendar apps, group calendars, an optional nightly backup with readable
`.ics`/`.vcf` files, and an HTTPRoute for the Gateway API.

RustiCal is a young single-maintainer project ("under active development"). Only SQLite is
supported — no PostgreSQL backend exists.

## Install

```sh
helm template dav charts/rustical -n dav -f /path/to/private-values.yaml
helm upgrade --install dav charts/rustical -n dav --create-namespace -f /path/to/private-values.yaml
```

Defaults render: password login, no OIDC, no backup, no route (users then come from
`rustical principals create <id> --password`). [ci/test-values.yaml](ci/test-values.yaml) shows a
full fictional setup.

## Things that bite

- **Service links:** a Service called `rustical` makes Kubernetes inject `RUSTICAL_*` variables
  and RustiCal refuses to start (upstream issue #122). The chart sets `enableServiceLinks: false`.
- **IPv6:** RustiCal binds `[::]` by default; on nodes without IPv6 that fails. The chart sets
  `RUSTICAL_HTTP__BIND=0.0.0.0:4000`.
- **No shell in the image** (`FROM scratch`). Administration runs the binary directly:
  `kubectl exec deploy/<release> -- /usr/local/bin/rustical principals list`.
- **SQLite on block storage**, never NFS. The database runs in WAL mode: copying the file is not a
  backup — use the backup job (SQLite backup API).

## Keycloak / OIDC

Verified 2026-10-03 with Keycloak 26.7.4 in a local cluster (throwaway realm), using a browser and
plain CalDAV/CardDAV requests:

- DAVx5's way in — Nextcloud login flow v2 → frontend → "Login with Keycloak" → "Authorize" →
  app token (64 characters) — works without any RustiCal password.
- A user outside `requireGroup` is rejected ("User is not in authorised group"), gets no token.
- With the token: create calendar and address book, store and read events and contacts; wrong
  token 401; another user's private calendar 401; data survives a pod restart.
- Group calendar: `assignMemberships` puts users of a Keycloak group into a RustiCal group
  principal on login; both members read and write its calendars, and DAVx5's discovery
  (`calendar-home-set`) lists it.

Keycloak side: a **confidential** client, Standard flow, redirect URI
`https://<host>/frontend/login/oidc/callback`, and a *Group Membership* mapper writing the claim
`groups` with **full path off** (otherwise the value is `/family`, not `family`). RustiCal's user
id is `preferred_username` by default (`:` and `$` are not allowed in it).

The group principal must exist before the first login of its members:
`rustical principals create family --principal-type group --name Family`. Memberships are only
added on login, never removed: remove them with `rustical principals membership remove`.
App tokens are stored as PBKDF2 hashes; revoke them in the frontend (profile) or with
`rustical principals app-token remove`.

## Backup

With `backup.enabled`, a CronJob (image `alpine/sqlite`) runs [files/backup.sh](files/backup.sh)
nightly into a second volume (e.g. on a NAS) and writes `aktuell/`:

| File | Content |
|---|---|
| `db.sqlite3` | consistent copy (SQLite backup API, while RustiCal runs), integrity-checked; everything incl. deleted items, groups and app tokens |
| `export/<user>/<calendar>.ics` | every live calendar as **one importable file** |
| `export/<user>/<addressbook>.vcf` | every live address book as one file |
| `INHALT.txt` | which file is which calendar (display names, counts) |

The job fails (exit 1) if the copy is damaged or the export misses any live object; the previous
state then stays untouched. It runs on the node of the RustiCal pod (`sameNodeAsApp`) because the
data volume is ReadWriteOnce.

**Restore, two ways — both verified against a real RustiCal 0.16.4 database:**

1. **Same server:** put `db.sqlite3` on the data volume (pod stopped) and start RustiCal. Users,
   groups, app tokens, calendars and contacts are back (deleted items are in the copy, too,
   marked as deleted).
2. **Anywhere else:** import the `.ics`/`.vcf` files into any calendar/contacts app or server.
   Into an empty RustiCal (method `IMPORT`, as its frontend does) this gave the same 7 objects
   with identical summaries, times, recurrences, alarms and contacts. One difference by design:
   each timezone appears once per calendar file, so an event whose own `VTIMEZONE` was
   incomplete gets the complete definition of another event with the same `TZID`.

## Tests

`tests/test-chart.py` (render: service links, IPv4 bind, OIDC environment, backup wiring,
rejected combinations) and `tests/test-backup.py` (the script against
`tests/fixtures/rustical-0.16.4-db.tar.gz`, a database written by RustiCal itself: recurring event
with timezone and alarm, VTODO, all-day event, deleted event, deleted calendar, group calendar,
vCard 3.0 and 4.0). The backup tests use a local `sqlite3` (CI) or the job's image via Docker;
they fail when neither exists. They fail against a script that duplicates timezones or exports
deleted events.
