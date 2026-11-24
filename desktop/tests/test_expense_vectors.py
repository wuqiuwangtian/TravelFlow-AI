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

        ec08 = by_id["EC08_limit_1999_to_2000_overflow_and_replay"]
        self.assertEqual(ec08["steps"][-1]["counts"]["expenses"], 2000)

        ec10 = by_id["EC10_unsupported_valid_fields_preserve_input"]
        self.assertEqual(len(ec10["steps"]), 6)
        self.assertTrue(all(step["preserve_input_for_retry"] for step in ec10["steps"]))

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

        # Mutating an expected outcome must fail with non-zero exit and name the scenario.
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


if __name__ == "__main__":
    unittest.main()
