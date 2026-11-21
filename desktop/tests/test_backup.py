"""Whole-file recovery, WAL consistency and failed destination protection."""

import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from desktop.backup import MANIFEST, SNAPSHOT, create_backup, restore_backup

FIXTURE = Path(__file__).parent / "fixtures" / "d1"


def unpack_sample(path):
    raw = gzip.decompress((FIXTURE / "fictional-trip.sqlite.gz").read_bytes())
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    if hashlib.sha256(raw).hexdigest() != expected["database_sha256"]:
        raise ValueError("Synthetic sample checksum mismatch")
    with path.open("xb") as out:
        out.write(raw)
    return expected


class BackupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="D1 中文 ")
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        self.source = self.folder / "fiction.sqlite"
        self.package = self.folder / "backup"
        self.target = self.folder / "copy"
        self.expected = unpack_sample(self.source)

    def test_online_copy_preserves_committed_wal_and_unknown_content(self):
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE future(value BLOB)")
            writer.execute("INSERT INTO future VALUES (x'00cafe')")
            writer.commit()
            writer.execute("DELETE FROM future")
            result = create_backup(self.source, self.package)
            writer.rollback()
        restored = restore_backup(self.package, self.target)
        self.assertEqual(result["sha256"], restored["sha256"])
        with closing(sqlite3.connect(restored["path"])) as db:
            self.assertEqual(db.execute("SELECT hex(value) FROM future").fetchall(), [("00CAFE",)])
            self.assertGreater(db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)
            db.execute("DELETE FROM future")
            db.commit()
        with closing(sqlite3.connect(self.source)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM future").fetchone()[0], 1)

    def test_existing_targets_and_invalid_sources(self):
        self.package.mkdir()
        marker = self.package / "keep"
        marker.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            create_backup(self.source, self.package)
        self.assertEqual(marker.read_bytes(), b"keep")
        with self.assertRaises(ValueError):
            create_backup(self.folder / "missing", self.target)
        with self.assertRaises(ValueError):
            create_backup(marker, self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.folder / "missing").exists())

    def test_failed_restore_keeps_originals_and_can_retry(self):
        create_backup(self.source, self.package)
        with self.assertRaises(ValueError):
            restore_backup(self.package, self.source)
        with self.assertRaises(ValueError):
            restore_backup(self.package, self.package / "nested")
        with (
            patch("desktop.backup.shutil.copyfileobj", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            restore_backup(self.package, self.target)
        self.assertFalse(self.target.exists())
        restore_backup(self.package, self.target)
        with self.assertRaises(ValueError):
            restore_backup(self.package, self.target)
        self.assertEqual(
            hashlib.sha256(self.source.read_bytes()).hexdigest(), self.expected["database_sha256"]
        )

    def test_killed_restore_never_publishes_partial_database(self):
        create_backup(self.source, self.package)
        worker = """
import sys, time
from pathlib import Path
from unittest.mock import patch
from desktop.backup import restore_backup
def pause_copy(src, dst):
    dst.write(src.read(128))
    dst.flush()
    time.sleep(60)
with patch("desktop.backup.shutil.copyfileobj", pause_copy):
    restore_backup(Path(sys.argv[1]), Path(sys.argv[2]))
"""
        with subprocess.Popen(
            [sys.executable, "-c", worker, str(self.package), str(self.target)],
            cwd=Path(__file__).resolve().parents[2],
        ) as process:
            try:
                partial = self.target / ".partial"
                deadline = time.monotonic() + 10
                while (
                    not partial.exists() or not partial.stat().st_size
                ) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(partial.exists() and partial.stat().st_size > 0)
            finally:
                process.kill()
                process.wait(timeout=10)
        self.assertFalse((self.target / "travel.sqlite").exists())
        with self.assertRaises(ValueError):
            restore_backup(self.package, self.target)
        restore_backup(self.package, self.folder / "retry")

    def test_torn_package_corruption_and_wrong_schema_rejected(self):
        create_backup(self.source, self.package)
        image = self.package / SNAPSHOT
        manifest = self.package / MANIFEST
        spec = json.loads(manifest.read_text())
        manifest.unlink()
        with self.assertRaises(ValueError):
            restore_backup(self.package, self.target)
        image.write_bytes(b"x" * spec["size"])
        manifest.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            restore_backup(self.package, self.target)
        image.unlink()
        with closing(sqlite3.connect(image)) as db:
            db.execute("CREATE TABLE unrelated(x)")
        spec.update(
            size=image.stat().st_size, sha256=hashlib.sha256(image.read_bytes()).hexdigest()
        )
        manifest.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError, "TravelFlow"):
            restore_backup(self.package, self.target)
        self.assertFalse(self.target.exists())
