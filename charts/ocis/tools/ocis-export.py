#!/usr/bin/env python3
"""Export the files of an oCIS storage (decomposedfs, messagepack metadata) to plain folders.

Works WITHOUT a running oCIS: reads the storage directory directly, e.g. a copy of the NAS
folder or a restore from a backup. Only Python 3 is needed, no packages.

    ocis-export.py <storage-root> <target-dir> [--versions] [--trash]

<storage-root> is the folder that contains `spaces/` (the chart's `files` volume,
STORAGE_USERS_OCIS_ROOT; without that volume: /var/lib/ocis/storage/users).
Every space becomes one folder in <target-dir>: "<name>" for project spaces,
"personal - <owner name>" for personal spaces. File names, folders and modification times are
restored; each file's SHA-1 is checked against the stored checksum ("checked" in the summary;
"unchecked" counts files without a stored checksum, e.g. empty ones).
--versions also writes older versions to "_versions/<path>/<timestamp> <name>",
--trash writes deleted items to "_trash/<original path>".

Only the metadata files (*.mpk) are read, not the symlinks in the node folders: backups made
with rclone skip symlinks by default. Exit code 1 if anything could not be exported or a
checksum does not match. Tested with oCIS 8.2.1.
"""
import argparse
import datetime
import hashlib
import os
import shutil
import struct
import sys
from pathlib import Path


# --- minimal MessagePack decoder (the subset decomposedfs writes) -----------------------------
def unpack(data):
    value, pos = _unpack(data, 0)
    if pos != len(data):
        raise ValueError(f"{len(data) - pos} trailing bytes")
    return value


def _unpack(b, i):
    t = b[i]
    i += 1
    if t <= 0x7F:
        return t, i
    if 0x80 <= t <= 0x8F:
        return _map(b, i, t & 0x0F)
    if 0x90 <= t <= 0x9F:
        return _array(b, i, t & 0x0F)
    if 0xA0 <= t <= 0xBF:
        n = t & 0x1F
        return b[i:i + n].decode("utf-8", "surrogateescape"), i + n
    if t >= 0xE0:
        return t - 0x100, i
    fixed = {0xC0: None, 0xC2: False, 0xC3: True}
    if t in fixed:
        return fixed[t], i
    sizes = {0xC4: ("B", 1), 0xC5: (">H", 2), 0xC6: (">I", 4)}  # bin 8/16/32
    if t in sizes:
        fmt, w = sizes[t]
        n = struct.unpack_from(fmt, b, i)[0]
        i += w
        return bytes(b[i:i + n]), i + n
    strs = {0xD9: ("B", 1), 0xDA: (">H", 2), 0xDB: (">I", 4)}
    if t in strs:
        fmt, w = strs[t]
        n = struct.unpack_from(fmt, b, i)[0]
        i += w
        return b[i:i + n].decode("utf-8", "surrogateescape"), i + n
    nums = {0xCA: ">f", 0xCB: ">d", 0xCC: "B", 0xCD: ">H", 0xCE: ">I", 0xCF: ">Q",
            0xD0: "b", 0xD1: ">h", 0xD2: ">i", 0xD3: ">q"}
    if t in nums:
        fmt = nums[t]
        return struct.unpack_from(fmt, b, i)[0], i + struct.calcsize(fmt)
    if t in (0xDC, 0xDD):
        n = struct.unpack_from(">H" if t == 0xDC else ">I", b, i)[0]
        return _array(b, i + (2 if t == 0xDC else 4), n)
    if t in (0xDE, 0xDF):
        n = struct.unpack_from(">H" if t == 0xDE else ">I", b, i)[0]
        return _map(b, i + (2 if t == 0xDE else 4), n)
    raise ValueError(f"unsupported msgpack type 0x{t:02x}")


def _map(b, i, n):
    out = {}
    for _ in range(n):
        k, i = _unpack(b, i)
        v, i = _unpack(b, i)
        out[k.decode() if isinstance(k, bytes) else k] = v
    return out, i


def _array(b, i, n):
    out = []
    for _ in range(n):
        v, i = _unpack(b, i)
        out.append(v)
    return out, i


# --- decomposedfs layout ----------------------------------------------------------------------
def text(attrs, key):
    v = attrs.get("user.ocis." + key)
    return v.decode("utf-8", "surrogateescape") if isinstance(v, bytes) else v


def split_path(base, ident):
    """IDs are stored as aa/bb/cc/dd/<rest> (first 8 characters in pairs)."""
    return base.joinpath(ident[0:2], ident[2:4], ident[4:6], ident[6:8], ident[8:])


def id_of(base, path):
    rel = path.relative_to(base).parts  # aa, bb, cc, dd, rest[.suffix]
    return "".join(rel[:4]) + rel[4]


def parse_time(value):
    if not value:
        return None
    s = value.rstrip("Z")
    if "." in s:  # nanoseconds -> microseconds
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


def safe(name):
    """A name from the metadata, usable as one path component."""
    name = (name or "").replace("/", "_").replace("\0", "")
    return "_" if name in ("", ".", "..") else name


class Exporter:
    def __init__(self, root, target, versions, trash):
        self.root, self.target = root, target
        self.versions, self.trash = versions, trash
        self.stats = {"spaces": 0, "folders": 0, "files": 0, "bytes": 0, "versions": 0,
                      "trashed": 0, "checked": 0, "unchecked": 0}
        self.errors = []

    def run(self):
        spaces = sorted(p for p in self.root.glob("spaces/*/*") if (p / "nodes").is_dir())
        if not spaces:
            sys.exit(f"no spaces below {self.root}/spaces — wrong storage root?")
        used = set()
        for space in spaces:
            self.export_space(space, used)
        return self.stats, self.errors

    def export_space(self, space, used):
        nodes_dir, blobs_dir = space / "nodes", space / "blobs"
        space_id = space.parent.name + space.name
        current, revisions, trashed = {}, [], []
        for mpk in nodes_dir.rglob("*.mpk"):
            base = mpk.name[:-4]
            try:
                attrs = unpack(mpk.read_bytes())
            except (ValueError, IndexError, struct.error) as e:
                self.errors.append(f"{mpk}: unreadable metadata ({e})")
                continue
            if ".REV." in base:
                revisions.append((id_of(nodes_dir, mpk.with_name(base.split(".REV.")[0])),
                                  base.split(".REV.")[1], attrs))
            elif ".T." in base:
                trashed.append((base.split(".T.")[1], attrs))
            else:
                attrs["_node"] = mpk.with_name(base)
                current[id_of(nodes_dir, mpk.with_name(base))] = attrs

        root = current.get(space_id)
        if root is None:
            self.errors.append(f"{space}: space root {space_id} not found")
            return
        kind, name = text(root, "space.type"), text(root, "space.name") or space_id
        folder = safe(f"personal - {name}" if kind == "personal" else name)
        while folder in used:
            folder += " (2)"
        used.add(folder)
        out = self.target / folder
        out.mkdir(parents=True, exist_ok=True)
        self.stats["spaces"] += 1

        paths = {space_id: Path()}

        def path_of(ident, seen=()):
            if ident in paths:
                return paths[ident]
            attrs = current.get(ident)
            if attrs is None or ident in seen:
                return None
            parent = path_of(text(attrs, "parentid"), seen + (ident,))
            if parent is None:
                return None
            paths[ident] = parent / safe(text(attrs, "name"))
            return paths[ident]

        dir_times = []
        for ident, attrs in current.items():
            if ident == space_id:
                continue
            rel = path_of(ident)
            if rel is None:
                self.errors.append(f"{folder}: {text(attrs, 'name')!r} ({ident}) has no "
                                   "reachable parent folder, skipped")
                continue
            dest = out / rel
            if text(attrs, "type") == "2" or attrs.get("user.ocis.type") == 2:  # container
                dest.mkdir(parents=True, exist_ok=True)
                ts = parse_time(text(attrs, "tmtime") or text(attrs, "mtime"))
                if ts is None and attrs["_node"].exists():
                    # A folder that never changed has no tmtime; oCIS shows the node's own
                    # mtime (lost if the backup did not keep folder times).
                    ts = attrs["_node"].stat().st_mtime
                dir_times.append((dest, ts))
                self.stats["folders"] += 1
            else:
                self.copy_blob(blobs_dir, attrs, dest, f"{folder}/{rel}")
                self.stats["files"] += 1

        if self.versions:
            for ident, stamp, attrs in revisions:
                rel = path_of(ident)
                if rel is None:
                    continue
                dest = out / "_versions" / rel.parent / f"{stamp.replace(':', '-')} {rel.name}"
                self.copy_blob(blobs_dir, attrs, dest, f"{folder}/{rel} @ {stamp}")
                self.stats["versions"] += 1

        if self.trash:
            for stamp, attrs in trashed:
                # trash.origin is the full original path, name included.
                origin = (text(attrs, "trash.origin") or text(attrs, "name") or "").strip("/")
                dest = out / "_trash" / Path(*[safe(p) for p in origin.split("/") if p])
                if text(attrs, "type") == "2":
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    self.copy_blob(blobs_dir, attrs, dest, f"{folder}/_trash/{origin}")
                self.stats["trashed"] += 1

        # Folder times last: writing files into a folder changes its mtime.
        for dest, ts in sorted(dir_times, key=lambda d: len(d[0].parts), reverse=True):
            if ts:
                os.utime(dest, (ts, ts))

    def copy_blob(self, blobs_dir, attrs, dest, label):
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob_id, size = text(attrs, "blobid"), attrs.get("user.ocis.blobsize")
        size = int(size.decode()) if isinstance(size, bytes) else size
        if not blob_id:
            if size:
                self.errors.append(f"{label}: no blob for {size} bytes")
                return
            dest.write_bytes(b"")
            self.stats["unchecked"] += 1
        else:
            src = split_path(blobs_dir, blob_id)
            if not src.is_file():
                self.errors.append(f"{label}: content missing ({src.relative_to(self.root)})")
                return
            shutil.copyfile(src, dest)
            expected = attrs.get("user.ocis.cs.sha1")
            if not (isinstance(expected, bytes) and len(expected) == 20):
                self.stats["unchecked"] += 1
            else:
                self.stats["checked"] += 1
                h = hashlib.sha1()
                with open(dest, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if h.digest() != expected:
                    self.errors.append(f"{label}: SHA-1 does not match the stored checksum")
        self.stats["bytes"] += dest.stat().st_size
        ts = parse_time(text(attrs, "mtime"))
        if ts:
            os.utime(dest, (ts, ts))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("storage_root", type=Path, help="folder containing spaces/")
    ap.add_argument("target", type=Path, help="export destination (created if missing)")
    ap.add_argument("--versions", action="store_true", help="also export older versions")
    ap.add_argument("--trash", action="store_true", help="also export the trash bin")
    args = ap.parse_args()
    if args.target.exists() and any(args.target.iterdir()):
        sys.exit(f"{args.target} is not empty")
    stats, errors = Exporter(args.storage_root.resolve(), args.target, args.versions,
                             args.trash).run()
    print("exported: " + ", ".join(f"{v} {k}" for k, v in stats.items()))
    for e in errors:
        print("ERROR:", e, file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
