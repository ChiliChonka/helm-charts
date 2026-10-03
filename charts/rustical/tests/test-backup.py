#!/usr/bin/env python3
"""files/backup.sh against a real RustiCal 0.16.4 database.

Fixture tests/fixtures/rustical-0.16.4-db.tar.gz was written by RustiCal itself over CalDAV/
CardDAV: users alice and carol, group principal "familie" with both as members; calendars
alice/privat (recurring event with timezone and alarm, second event with the same timezone, one
deleted event), alice/arbeit (all-day event, VTODO), alice/alt (deleted calendar with one event),
familie/gemeinsam (event written by carol); address book alice/kontakte (vCard 3.0 and 4.0).

Runs the script with a local sqlite3 (CI: Ubuntu, GNU tools, mawk) or, without one, inside the
backup job's image (busybox). Fails if neither is available.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "files" / "backup.sh"
FIXTURE = HERE / "fixtures" / "rustical-0.16.4-db.tar.gz"
IMAGE = "docker.io/alpine/sqlite:3.53.4"


def run_backup(data, out):
    if shutil.which("sqlite3"):
        return subprocess.run(["sh", str(SCRIPT)], env={"DB": str(data / "db.sqlite3"), "OUT": str(out),
                                                        "PATH": "/usr/bin:/bin"},
                              text=True, capture_output=True)
    if shutil.which("docker"):
        return subprocess.run(["docker", "run", "--rm", "-u", "1000:1000", "-e", "DB=/data/db.sqlite3",
                               "-e", "OUT=/backup", "-v", f"{data}:/data", "-v", f"{out}:/backup",
                               "-v", f"{SCRIPT}:/backup.sh:ro", "--entrypoint", "sh", IMAGE, "/backup.sh"],
                              text=True, capture_output=True)
    raise RuntimeError("neither sqlite3 nor docker available — cannot test the backup script")


def sql(db, query):
    if shutil.which("sqlite3"):
        return subprocess.run(["sqlite3", str(db), query], text=True, capture_output=True, check=True).stdout
    return subprocess.run(["docker", "run", "--rm", "-v", f"{db.parent}:/d", IMAGE, f"/d/{db.name}", query],
                          text=True, capture_output=True, check=True).stdout


class BackupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data, self.out = Path(tmp.name) / "data", Path(tmp.name) / "out"
        self.data.mkdir()
        self.out.mkdir()
        with tarfile.open(FIXTURE) as t:
            t.extractall(self.data, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(self.data)
        for p in (self.data, self.out):  # the container runs as uid 1000
            p.chmod(0o777)
        (self.data / "db.sqlite3").chmod(0o666)

    def backup_ok(self):
        r = run_backup(self.data, self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_exports_every_live_object_and_nothing_deleted(self):
        out = self.backup_ok()
        self.assertIn("ok: 4 Sammlungen, 7 Objekte", out)
        export = self.out / "aktuell" / "export"
        files = sorted(str(p.relative_to(export)) for p in export.rglob("*") if p.is_file())
        self.assertEqual(files, ["alice/arbeit.ics", "alice/kontakte.vcf", "alice/privat.ics",
                                 "familie/gemeinsam.ics"])
        uids = lambda f: sorted(re.findall(r"^UID:(.*)\r$", (export / f).read_bytes().decode(), re.M))
        self.assertEqual(uids("alice/privat.ics"), ["arzt-1", "sport-1"])
        self.assertEqual(uids("alice/arbeit.ics"), ["todo-1", "urlaub-1"])
        self.assertEqual(uids("familie/gemeinsam.ics"), ["grill-1"])
        self.assertEqual(uids("alice/kontakte.vcf"), ["oma", "opa"])
        everything = "".join(p.read_text() for p in export.rglob("*") if p.is_file())
        self.assertNotIn("weg-1", everything)   # deleted event
        self.assertNotIn("alt-1", everything)   # event in a deleted calendar

    def test_calendar_file_is_one_valid_vcalendar(self):
        self.backup_ok()
        raw = (self.out / "aktuell" / "export" / "alice" / "privat.ics").read_bytes()
        lines = raw.split(b"\r\n")
        self.assertEqual(lines[-1], b"")                       # ends with CRLF
        self.assertNotIn(b"\n", b"".join(lines))               # only CRLF line breaks
        text = raw.decode()
        self.assertEqual(text.count("BEGIN:VCALENDAR"), 1)
        self.assertEqual(text.count("END:VCALENDAR"), 1)
        self.assertEqual(text.count("BEGIN:VTIMEZONE"), 1)     # shared by both events
        self.assertEqual(text.count("BEGIN:VEVENT"), 2)
        self.assertEqual(text.count("BEGIN:VALARM"), 1)
        self.assertIn("RRULE:FREQ=WEEKLY;BYDAY=TU", text)
        self.assertIn("SUMMARY:Tischtennis Training", text)
        for begin in re.findall(r"^BEGIN:(\w+)\r$", text, re.M):
            self.assertEqual(text.count(f"BEGIN:{begin}\r\n"), text.count(f"END:{begin}\r\n"), begin)

    def test_database_copy_is_complete_and_consistent(self):
        self.backup_ok()
        copy = self.out / "aktuell" / "db.sqlite3"
        self.assertEqual(sql(copy, "PRAGMA integrity_check;").strip(), "ok")
        # the copy keeps everything, deleted items and app tokens included
        self.assertEqual(sql(copy, "SELECT count(*) FROM calendarobjects;").strip(), "7")
        self.assertEqual(sql(copy, "SELECT count(*) FROM app_tokens;").strip(), "3")  # alice 2, carol 1
        index = (self.out / "aktuell" / "INHALT.txt").read_text()
        self.assertIn("Familienkalender", index)

    def test_second_run_replaces_and_leaves_no_temp(self):
        self.backup_ok()
        self.backup_ok()
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["aktuell"])

    def test_failure_keeps_previous_state(self):
        self.backup_ok()
        before = (self.out / "aktuell" / "INHALT.txt").read_text()
        (self.data / "db.sqlite3").unlink()
        r = run_backup(self.data, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fehlt", r.stdout)
        self.assertEqual((self.out / "aktuell" / "INHALT.txt").read_text(), before)


if __name__ == "__main__":
    unittest.main(verbosity=1)
