#!/usr/bin/env python3
"""tools/ocis-export.py against a real oCIS 8.2.1 storage (no cluster needed).

Fixture: tests/fixtures/decomposedfs-8.2.1.tar.gz is the files volume of an oCIS 8.2.1 pod after
uploading test data over WebDAV: nested folders, an empty folder and file, umlauts, a file
overwritten twice (2 versions), a renamed file, a deleted file (trash) and a project space.
The .expected.tsv next to it is what oCIS itself served over WebDAV for the same data:
path, sha256 (or DIR) and modification time.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
EXPORT = HERE.parent / "tools" / "ocis-export.py"
FIXTURE = HERE / "fixtures" / "decomposedfs-8.2.1.tar.gz"
EXPECTED = HERE / "fixtures" / "decomposedfs-8.2.1.expected.tsv"


def extract(dest):
    with tarfile.open(FIXTURE) as t:
        if hasattr(tarfile, "fully_trusted_filter"):
            t.extractall(dest, filter="fully_trusted")  # keeps symlinks and folder times
        else:
            t.extractall(dest)


def run(*args):
    return subprocess.run([sys.executable, str(EXPORT), *map(str, args)],
                          text=True, capture_output=True)


def tree(root):
    """Same format as the expected file; _versions/_trash are not part of oCIS's view."""
    rows = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("_versions", "_trash")]
        if Path(d) != Path(root):
            for x in dirs:
                p = Path(d) / x
                rows.append(f"{p.relative_to(root)}\tDIR\t{int(p.stat().st_mtime)}")
        else:
            for x in dirs:
                pass  # space folders: oCIS shows no own entry for them
        for x in files:
            p = Path(d) / x
            rows.append(f"{p.relative_to(root)}\t{hashlib.sha256(p.read_bytes()).hexdigest()}"
                        f"\t{int(p.stat().st_mtime)}")
    return sorted(rows)


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / "store"
        self.out = Path(self.tmp.name) / "out"
        extract(self.store)

    def test_matches_what_ocis_served(self):
        expected = sorted(EXPECTED.read_text().splitlines())
        self.assertEqual(len(expected), 11)  # the fixture really has content
        r = run(self.store, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tree(self.out), expected)
        self.assertIn("2 spaces, 5 folders, 6 files", r.stdout)
        self.assertIn("5 checked, 1 unchecked", r.stdout)  # the empty file has no checksum

    def test_versions_and_trash(self):
        r = run(self.store, self.out, "--versions", "--trash")
        self.assertEqual(r.returncode, 0, r.stderr)
        versions = sorted((self.out / "personal - Admin" / "_versions").iterdir())
        self.assertEqual([v.read_text() for v in versions],
                         ["Einkaufsliste Version 1\n", "Einkaufsliste Version 2\n"])
        self.assertTrue(all(v.name.endswith(" Einkaufsliste.txt") for v in versions))
        trashed = self.out / "personal - Admin" / "_trash" / "Dokumente" / "geloescht.txt"
        self.assertEqual(trashed.read_text(), "weg damit\n")
        self.assertIn("8 checked, 1 unchecked", r.stdout)
        # current files unchanged by the options
        expected = sorted(EXPECTED.read_text().splitlines())
        self.assertEqual([row.split("\t")[:2] for row in tree(self.out)],
                         [row.split("\t")[:2] for row in expected])

    def test_backup_without_symlinks(self):
        """rclone skips symlinks by default; the export must not depend on them."""
        links = [p for p in self.store.rglob("*") if p.is_symlink()]
        self.assertGreater(len(links), 5)
        for p in links:
            p.unlink()
        r = run(self.store, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = sorted(EXPECTED.read_text().splitlines())
        self.assertEqual([row.split("\t")[:2] for row in tree(self.out)],
                         [row.split("\t")[:2] for row in expected])

    def test_corrupted_content_is_reported(self):
        blobs = [p for p in self.store.rglob("*") if "blobs" in p.parts and p.is_file()
                 and p.stat().st_size > 1000]
        self.assertEqual(len(blobs), 1)
        data = bytearray(blobs[0].read_bytes())
        data[500] ^= 0xFF
        blobs[0].write_bytes(bytes(data))
        r = run(self.store, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("2025.pdf: SHA-1 does not match", r.stderr)

    def test_missing_content_is_reported(self):
        blob = next(p for p in self.store.rglob("*") if "blobs" in p.parts and p.is_file()
                    and p.stat().st_size > 1000)
        blob.unlink()
        r = run(self.store, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("2025.pdf: content missing", r.stderr)

    def test_refuses_non_empty_target_and_wrong_root(self):
        self.out.mkdir()
        (self.out / "x").write_text("x")
        r = run(self.store, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not empty", r.stderr)
        r = run(self.store / "spaces", Path(self.tmp.name) / "out2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("wrong storage root", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
