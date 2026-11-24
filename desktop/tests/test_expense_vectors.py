"""Acceptance tests for the portable Phase 2 expense.create vectors and desktop checker."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from desktop.expense_vectors import DEFAULT_VECTORS, REPO_ROOT, run_vectors


class ExpenseVectorsTests(unittest.TestCase):
    def test_all_portable_vectors_pass_against_real_desktop_executor(self):
        report = run_vectors(DEFAULT_VECTORS)
        self.assertTrue(report["all_passed"], json.dumps(report["failed_scenarios"]))
        self.assertEqual(report["total_scenarios"], 10)
        self.assertEqual(report["passed_scenarios"], 10)
        self.assertIsNone(report["shared_contract_version"])

        by_id = {sc["id"]: sc for sc in report["scenarios"]}
        ec03 = by_id["EC03_interrupt_after_commit_and_replay_after_head_advance"]
        self.assertIn("R1", ec03["bindings"])
        self.assertIn("R2", ec03["bindings"])
        self.assertNotEqual(ec03["bindings"]["R1"], ec03["bindings"]["R2"])
        # After-commit abort persists canonical intent in SQLite across reopen.
        self.assertTrue(ec03["steps"][0]["input_retention"]["cross_restart_persistence_verified"])

        ec08 = by_id["EC08_limit_1999_to_2000_overflow_and_replay"]
        self.assertEqual(ec08["steps"][-1]["counts"]["expenses"], 2000)

        # Unsupported valid fields require input retention, are canonically reusable in memory,
        # and honestly report cross-restart persistence as unverified (no pending client store).
        ec10 = by_id["EC10_unsupported_valid_fields_preserve_input"]
        self.assertEqual(len(ec10["steps"]), 6)
        for step in ec10["steps"]:
            ret = step["input_retention"]
            self.assertTrue(ret["required_by_candidate"])
            self.assertTrue(ret["reusable_in_caller_memory"])
            self.assertIsNotNone(ret["submitted_input_sha256"])
            self.assertIsNotNone(ret["reusable_canonical_sha256"])
            self.assertFalse(ret["cross_restart_persistence_verified"])
            self.assertIn("unverified across restart", ret["cross_restart_note"])

    def test_cli_exits_zero_on_valid_vectors_and_nonzero_on_mismatch(self):
        ok_proc = subprocess.run(
            [sys.executable, "-m", "desktop.expense_vectors", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(ok_proc.returncode, 0, ok_proc.stderr)
        payload = json.loads(ok_proc.stdout)
        self.assertTrue(payload["all_passed"])

        with tempfile.TemporaryDirectory(prefix="p2-vec-fail-") as tmp:
            bad_vectors = Path(tmp) / "bad_vectors.json"
            spec = json.loads(DEFAULT_VECTORS.read_bytes().decode("utf-8"))
            spec["scenarios"][0]["steps"][0]["expected"]["desktop_raw"]["error"] = "unexpected_code"
            bad_vectors.write_bytes(json.dumps(spec, ensure_ascii=False, indent=2).encode("utf-8"))

            fail_proc = subprocess.run(
                [sys.executable, "-m", "desktop.expense_vectors", "--vectors", str(bad_vectors)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(fail_proc.returncode, 0)
            self.assertIn("[FAIL] EC01_first_create", fail_proc.stdout)

    def test_negative_control_amount_plus_one_cent_fails_and_exits_nonzero(self):
        """Injecting +1 cent into persisted expenses.amount_cents must fail checker."""
        driver = """
import sqlite3, sys
from contextlib import closing
import desktop.expense_vectors as ev

real_execute = ev.execute_command

def buggy_execute(db_path, raw_command, **kwargs):
    res = real_execute(db_path, raw_command, **kwargs)
    if res.get("ok") and not res.get("replayed"):
        entity_id = res["receipt"]["entity_id"]
        with closing(sqlite3.connect(db_path)) as db:
            db.execute(
                "UPDATE expenses SET amount_cents = amount_cents + 1 WHERE entity_id = ?",
                (entity_id,),
            )
            db.commit()
    return res

ev.execute_command = buggy_execute
raise SystemExit(ev.main([]))
"""
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("[FAIL] EC01_first_create", proc.stdout)
        self.assertIn("amount_cents=8636 != 8635", proc.stdout)

    def test_negative_control_unauthorized_with_leaked_receipt_fails_and_exits_nonzero(self):
        """Injecting a leaked receipt on unauthorized responses must fail checker."""
        driver = """
import desktop.expense_vectors as ev

real_execute = ev.execute_command

def leaky_execute(db_path, raw_command, **kwargs):
    res = real_execute(db_path, raw_command, **kwargs)
    if res.get("ok") is False and res.get("error") == "unauthorized":
        res = {**res, "receipt": {"result_revision_id": "leaked-rev"}}
    return res

ev.execute_command = leaky_execute
raise SystemExit(ev.main([]))
"""
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("[FAIL] EC06_wrong_target_and_lookup_authorization", proc.stdout)
        self.assertIn("leaked forbidden keys: ['receipt']", proc.stdout)


if __name__ == "__main__":
    unittest.main()
