"""Unit tests for expense-patch-candidate 0.1.0-candidate.1 bundle and reference executor."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from desktop.expense_patch_candidate import (
    DEFAULT_BUNDLE_DIR,
    verify_bundle,
)


class ExpensePatchCandidateTests(unittest.TestCase):
    def test_candidate_bundle_passes_all_scenarios_and_digests(self) -> None:
        summary = verify_bundle(DEFAULT_BUNDLE_DIR)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["contract_id"], "travelflow.expense-patch-candidate")
        self.assertEqual(summary["contract_version"], "0.1.0-candidate.1")
        self.assertEqual(summary["scenarios"], 9)
        self.assertEqual(summary["steps"], 19)

    def test_tampered_vector_amount_fails_non_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy_dir = Path(tmp) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIR, copy_dir)
            vec_path = copy_dir / "normative-vectors.json"
            vec_path.write_text(
                vec_path.read_text(encoding="utf-8").replace("2150", "2159", 1),
                encoding="utf-8",
            )
            with self.assertRaises(AssertionError):
                verify_bundle(copy_dir)

    def test_missing_bundle_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy_dir = Path(tmp) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIR, copy_dir)
            (copy_dir / "result-mapping.json").unlink()
            with self.assertRaises(AssertionError):
                verify_bundle(copy_dir)

    def test_manifest_digest_matches_exact_bytes(self) -> None:
        manifest = json.loads((DEFAULT_BUNDLE_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "candidate")
        self.assertIsNone(manifest["shared_contract_version"])
        self.assertEqual(manifest["base_commit"], "fdefebe")


if __name__ == "__main__":
    unittest.main()
