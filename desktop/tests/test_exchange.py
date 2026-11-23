"""Read-only candidate exchange export, independent verification, and corruption rejection tests."""

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

from desktop.exchange import (
    EXCHANGE,
    FORMAT,
    MANIFEST,
    STATUS,
    export_exchange_bundle,
    verify_exchange_bundle,
)

FIXTURE = Path(__file__).parent / "fixtures" / "d1"
SAMPLE_GZ = FIXTURE / "fictional-trip.sqlite.gz"
EXPECTED_JSON = FIXTURE / "expected.json"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _unpack_fixture_copy(target_path):
    raw = gzip.decompress(SAMPLE_GZ.read_bytes())
    target_path.write_bytes(raw)
    return raw


class ExchangeExportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="D1 exchange ")
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()

    def test_fixed_fixture_export_and_independent_expected_assertions(self):
        before_gz_sha = hashlib.sha256(SAMPLE_GZ.read_bytes()).hexdigest()
        before_fixture_files = sorted(p.name for p in FIXTURE.iterdir())
        bundle_dir = self.folder / "bundle"

        summary = export_exchange_bundle(SAMPLE_GZ, bundle_dir)
        self.assertEqual(summary["format"], FORMAT)
        self.assertEqual(summary["status"], STATUS)
        self.assertIsNone(summary["shared_contract_version"])
        self.assertEqual(summary["sessions"], 1)
        self.assertEqual(summary["branches"], 2)
        self.assertEqual(summary["versions"], 4)
        self.assertEqual(summary["attachments"], 3)

        # Source fixture file and directory must remain completely untouched.
        self.assertEqual(hashlib.sha256(SAMPLE_GZ.read_bytes()).hexdigest(), before_gz_sha)
        self.assertEqual(sorted(p.name for p in FIXTURE.iterdir()), before_fixture_files)

        verified = verify_exchange_bundle(bundle_dir, expected_path=EXPECTED_JSON)
        self.assertTrue(verified["verified"])
        self.assertTrue(verified["expected_checked"])

        expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))
        exchange = json.loads((bundle_dir / EXCHANGE).read_text(encoding="utf-8"))
        self.assertEqual(exchange["source"]["sqlite_application_id"], 0)
        self.assertEqual(exchange["source"]["database_sha256"], expected["database_sha256"])
        self.assertIn("raw_itinerary_json", exchange["field_sources"])
        self.assertTrue(exchange["uncovered_items"])

        branches = {b["id"]: b for b in exchange["branches"]}
        self.assertEqual(
            branches[expected["main_branch"]]["version_ids"], expected["main_versions"]
        )
        self.assertEqual(
            branches[expected["fork_branch"]]["version_ids"], expected["fork_versions"]
        )
        self.assertEqual(
            branches[expected["fork_branch"]]["fork_version_id"], expected["fork_from"]
        )
        self.assertEqual(
            branches[expected["fork_branch"]]["parent_branch_id"], expected["main_branch"]
        )

        versions = {v["id"]: v for v in exchange["versions"]}
        self.assertEqual(
            versions[expected["fork_versions"][0]]["parent_relation"]["parent_version_id"],
            expected["fork_from"],
        )
        self.assertEqual(
            versions[expected["fork_versions"][0]]["parent_relation"]["kind"], "fork_version_id"
        )
        self.assertEqual(versions[expected["main_versions"][0]]["expenses_total_cents"], 0)
        self.assertEqual(
            versions[expected["main_versions"][1]]["expenses_total_cents"], expected["amount_cents"]
        )
        self.assertEqual(
            versions[expected["main_versions"][2]]["expenses_total_cents"], expected["amount_cents"]
        )
        self.assertEqual(versions[expected["fork_versions"][0]]["expenses_total_cents"], 0)

        self.assertEqual(
            versions[expected["main_versions"][1]]["attachment_refs"]["receipt_ids"],
            [expected["receipt"]],
        )
        self.assertEqual(
            versions[expected["main_versions"][2]]["attachment_refs"]["photo_ids"],
            [expected["photo"]],
        )
        self.assertEqual(
            versions[expected["main_versions"][2]]["attachment_refs"]["cover_photo_id"],
            expected["photo"],
        )
        self.assertEqual(verified["attachments_sha256"], expected["attachments_sha256"])

    def test_unknown_json_fields_are_preserved_and_missing_vs_null_kept(self):
        db_copy = self.folder / "unknown-fields.sqlite"
        _unpack_fixture_copy(db_copy)
        expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))
        v2 = expected["main_versions"][1]

        with closing(sqlite3.connect(db_copy)) as db:
            raw = db.execute("SELECT itinerary FROM plans WHERE version_id=?", (v2,)).fetchone()[0]
            data = json.loads(raw)
            data["future_top_level"] = {"nested": [1, "keep", None]}
            data["expenses"][0]["currency"] = None
            data["expenses"][0]["paid_on"] = None
            data["expenses"][0]["vendor_extension"] = "opaque-tag"
            db.execute(
                "UPDATE plans SET itinerary=? WHERE version_id=?",
                (json.dumps(data, ensure_ascii=False), v2),
            )
            db.commit()

        before_sha = hashlib.sha256(db_copy.read_bytes()).hexdigest()
        bundle_dir = self.folder / "bundle-unknown"
        export_exchange_bundle(db_copy, bundle_dir)
        verify_exchange_bundle(bundle_dir)

        # Read-only export must not alter the source SQLite or create sidecar files.
        self.assertEqual(hashlib.sha256(db_copy.read_bytes()).hexdigest(), before_sha)
        self.assertFalse((self.folder / "unknown-fields.sqlite-wal").exists())
        self.assertFalse((self.folder / "unknown-fields.sqlite-shm").exists())

        raw_exported = json.loads((bundle_dir / "itineraries" / f"{v2}.json").read_text("utf-8"))
        self.assertEqual(raw_exported["future_top_level"], {"nested": [1, "keep", None]})
        self.assertIn("currency", raw_exported["expenses"][0])
        self.assertIsNone(raw_exported["expenses"][0]["currency"])
        self.assertIn("paid_on", raw_exported["expenses"][0])
        self.assertIsNone(raw_exported["expenses"][0]["paid_on"])
        self.assertEqual(raw_exported["expenses"][0]["vendor_extension"], "opaque-tag")

        exchange = json.loads((bundle_dir / EXCHANGE).read_text(encoding="utf-8"))
        v2_out = next(v for v in exchange["versions"] if v["id"] == v2)
        self.assertIn("currency", v2_out["expenses"][0])
        self.assertIsNone(v2_out["expenses"][0]["currency"])
        self.assertEqual(v2_out["expenses"][0]["vendor_extension"], "opaque-tag")

    def test_duplicate_json_keys_bad_json_and_bool_amount_rejected_without_artifacts(self):
        expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))
        v2 = expected["main_versions"][1]
        cases = [
            ("duplicate_key", '{"days":[],"expenses":[],"dup":1,"dup":2}', "Duplicate JSON key"),
            ("invalid_json", "{not-json", "not valid JSON"),
            ("non_object", '["array"]', "JSON object"),
            (
                "bool_amount",
                '{"days":[],"expenses":[{"id":"e1","amount_cents":true}]}',
                "non-negative int",
            ),
        ]
        for name, bad_itinerary, pattern in cases:
            with self.subTest(case=name):
                db_copy = self.folder / f"{name}.sqlite"
                _unpack_fixture_copy(db_copy)
                with closing(sqlite3.connect(db_copy)) as db:
                    db.execute(
                        "UPDATE plans SET itinerary=? WHERE version_id=?", (bad_itinerary, v2)
                    )
                    db.commit()
                out_dir = self.folder / f"out-{name}"
                with self.assertRaisesRegex(ValueError, pattern):
                    export_exchange_bundle(db_copy, out_dir)
                self.assertFalse(out_dir.exists())

    def test_corrupt_gzip_corrupt_sqlite_and_dangling_references_rejected(self):
        bad_gz = self.folder / "bad.sqlite.gz"
        bad_gz.write_bytes(b"not a gzip stream")
        out_gz = self.folder / "out-bad-gz"
        with self.assertRaisesRegex(ValueError, "gzip"):
            export_exchange_bundle(bad_gz, out_gz)
        self.assertFalse(out_gz.exists())

        bad_db = self.folder / "bad.sqlite"
        bad_db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)
        out_db = self.folder / "out-bad-db"
        with self.assertRaises(ValueError):
            export_exchange_bundle(bad_db, out_db)
        self.assertFalse(out_db.exists())

        wrong_schema = self.folder / "wrong-schema.sqlite"
        with closing(sqlite3.connect(wrong_schema)) as db:
            db.execute("CREATE TABLE unrelated(x INTEGER)")
            db.commit()
        out_schema = self.folder / "out-wrong-schema"
        with self.assertRaisesRegex(ValueError, "missing required tables"):
            export_exchange_bundle(wrong_schema, out_schema)
        self.assertFalse(out_schema.exists())

        dangling_receipt = self.folder / "dangling-receipt.sqlite"
        _unpack_fixture_copy(dangling_receipt)
        with closing(sqlite3.connect(dangling_receipt)) as db:
            db.execute("DELETE FROM receipt_assets")
            db.commit()
        out_dangling = self.folder / "out-dangling"
        with self.assertRaisesRegex(ValueError, "missing receipt asset"):
            export_exchange_bundle(dangling_receipt, out_dangling)
        self.assertFalse(out_dangling.exists())

    def test_existing_output_dir_and_mid_write_failure_leave_no_usable_artifact(self):
        existing = self.folder / "existing"
        existing.mkdir()
        keep = existing / "keep.txt"
        keep.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_exchange_bundle(SAMPLE_GZ, existing)
        self.assertEqual(keep.read_text(encoding="utf-8"), "preserve")

        failed_out = self.folder / "disk-full"
        real_write = Path.open
        calls = 0

        def fail_on_later_open(path_self, *args, **kwargs):
            nonlocal calls
            if "x" in (args[0] if args else kwargs.get("mode", "")):
                calls += 1
                if calls == 3:
                    raise OSError("disk full")
            return real_write(path_self, *args, **kwargs)

        with patch.object(Path, "open", fail_on_later_open), self.assertRaises(OSError):
            export_exchange_bundle(SAMPLE_GZ, failed_out)
        self.assertFalse(failed_out.exists())

        # Retry succeeds cleanly after failure.
        export_exchange_bundle(SAMPLE_GZ, failed_out)
        verify_exchange_bundle(failed_out, expected_path=EXPECTED_JSON)

    def test_killed_export_never_publishes_usable_manifest_and_tampering_rejected(self):
        killed_out = self.folder / "killed-bundle"
        worker = """
import sys, time
from pathlib import Path
from unittest.mock import patch
import desktop.exchange as ex
real_write = ex._write_bytes_exact
def slow_write(path, data):
    res = real_write(path, data)
    if path.name == "exchange.json":
        time.sleep(60)
    return res
with patch.object(ex, "_write_bytes_exact", slow_write):
    ex.export_exchange_bundle(Path(sys.argv[1]), Path(sys.argv[2]))
"""
        with subprocess.Popen(
            [sys.executable, "-c", worker, str(SAMPLE_GZ), str(killed_out)],
            cwd=REPO_ROOT,
        ) as proc:
            try:
                exchange_file = killed_out / EXCHANGE
                deadline = time.monotonic() + 10
                while not exchange_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(exchange_file.exists())
            finally:
                proc.kill()
                proc.wait(timeout=10)

        self.assertFalse((killed_out / MANIFEST).exists())
        with self.assertRaisesRegex(ValueError, "missing manifest.json"):
            verify_exchange_bundle(killed_out)

        good_out = self.folder / "tamper-bundle"
        export_exchange_bundle(SAMPLE_GZ, good_out)
        expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))
        att_file = good_out / "attachments" / "travel-photos" / f"{expected['photo']}.bin"
        raw_att = att_file.read_bytes()
        att_file.write_bytes(bytes([raw_att[0] ^ 0xFF]) + raw_att[1:])
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            verify_exchange_bundle(good_out)

    def test_cli_export_and_verify(self):
        bundle_dir = self.folder / "cli-bundle"
        export_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "desktop.exchange",
                "export",
                "--source",
                str(SAMPLE_GZ),
                "--output",
                str(bundle_dir),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(export_proc.returncode, 0, export_proc.stderr)
        exported = json.loads(export_proc.stdout)
        self.assertEqual(exported["versions"], 4)

        verify_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "desktop.exchange",
                "verify",
                "--bundle",
                str(bundle_dir),
                "--expected",
                str(EXPECTED_JSON),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(verify_proc.returncode, 0, verify_proc.stderr)
        verified = json.loads(verify_proc.stdout)
        self.assertTrue(verified["verified"])
        self.assertTrue(verified["expected_checked"])

    def test_plain_sqlite_with_companion_wal_or_journal_is_rejected(self):
        wal_db = self.folder / "active-wal.sqlite"
        _unpack_fixture_copy(wal_db)
        out_wal = self.folder / "out-active-wal"
        with closing(sqlite3.connect(wal_db)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE sessions SET title = '已提交未合并标题'")
            writer.commit()
            self.assertTrue(Path(f"{wal_db}-wal").exists())
            with self.assertRaisesRegex(ValueError, "companion journal/WAL sidecar"):
                export_exchange_bundle(wal_db, out_wal)
        self.assertFalse(out_wal.exists())

        journal_db = self.folder / "stale-journal.sqlite"
        _unpack_fixture_copy(journal_db)
        Path(f"{journal_db}-journal").write_bytes(b"stale-journal")
        out_journal = self.folder / "out-stale-journal"
        with self.assertRaisesRegex(ValueError, "companion journal/WAL sidecar"):
            export_exchange_bundle(journal_db, out_journal)
        self.assertFalse(out_journal.exists())

    def test_crlf_itinerary_round_trips_and_verifies_by_raw_bytes(self):
        crlf_db = self.folder / "crlf.sqlite"
        _unpack_fixture_copy(crlf_db)
        expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))
        v2 = expected["main_versions"][1]

        with closing(sqlite3.connect(crlf_db)) as db:
            raw = db.execute("SELECT itinerary FROM plans WHERE version_id=?", (v2,)).fetchone()[0]
            crlf_json = json.dumps(json.loads(raw), ensure_ascii=False, indent=2).replace(
                "\n", "\r\n"
            )
            db.execute("UPDATE plans SET itinerary=? WHERE version_id=?", (crlf_json, v2))
            db.commit()

        bundle_dir = self.folder / "bundle-crlf"
        export_exchange_bundle(crlf_db, bundle_dir)
        verified = verify_exchange_bundle(bundle_dir)
        self.assertTrue(verified["verified"])

        exported_bytes = (bundle_dir / "itineraries" / f"{v2}.json").read_bytes()
        self.assertIn(b"\r\n", exported_bytes)
        self.assertEqual(exported_bytes, crlf_json.encode("utf-8"))

    def test_verify_rejects_contradictory_branch_version_ownership_duplicates_and_ancestors(self):
        expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))

        def mutate_bundle(name, mutate_fn):
            bundle_dir = self.folder / f"mut-{name}"
            export_exchange_bundle(SAMPLE_GZ, bundle_dir)
            exchange_path = bundle_dir / EXCHANGE
            manifest_path = bundle_dir / MANIFEST
            exchange = json.loads(exchange_path.read_bytes().decode("utf-8"))
            manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
            mutate_fn(exchange, manifest)
            new_exchange_bytes = (
                json.dumps(exchange, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            exchange_path.write_bytes(new_exchange_bytes)
            manifest["files"][EXCHANGE] = {
                "size": len(new_exchange_bytes),
                "sha256": hashlib.sha256(new_exchange_bytes).hexdigest(),
            }
            manifest_path.write_bytes(
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            return bundle_dir

        def swap_version_branch(exchange, _manifest):
            for ver in exchange["versions"]:
                if ver["id"] == expected["main_versions"][1]:
                    ver["branch_id"] = expected["fork_branch"]

        def duplicate_version(exchange, manifest):
            exchange["versions"].append(dict(exchange["versions"][0]))
            manifest["summary"]["versions"] = len(exchange["versions"])

        def orphan_version(exchange, _manifest):
            for branch in exchange["branches"]:
                if branch["id"] == expected["main_branch"]:
                    branch["version_ids"].pop()

        def bad_fork_ancestor(exchange, _manifest):
            for ver in exchange["versions"]:
                if ver["id"] == expected["fork_versions"][0]:
                    ver["parent_relation"]["parent_version_id"] = expected["main_versions"][1]

        def bad_seq_predecessor(exchange, _manifest):
            for ver in exchange["versions"]:
                if ver["id"] == expected["main_versions"][2]:
                    ver["parent_relation"]["parent_version_id"] = expected["main_versions"][0]

        cases = [
            ("mismatched_branch_id", swap_version_branch, "contradicts branch"),
            ("duplicate_version", duplicate_version, "Duplicate version id"),
            ("orphan_version", orphan_version, "missing from their branch"),
            ("bad_fork_ancestor", bad_fork_ancestor, "contradicts branch"),
            ("bad_seq_predecessor", bad_seq_predecessor, "contradicts predecessor"),
        ]
        for name, mutator, pattern in cases:
            with self.subTest(case=name):
                mutated = mutate_bundle(name, mutator)
                with self.assertRaisesRegex(ValueError, pattern):
                    verify_exchange_bundle(mutated, expected_path=EXPECTED_JSON)


if __name__ == "__main__":
    unittest.main()
