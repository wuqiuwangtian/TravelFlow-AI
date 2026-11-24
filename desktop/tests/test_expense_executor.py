"""Isolated SQLite persistent executor, crash-boundary, and concurrency acceptance tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from desktop.expense_executor import (
    CANONICAL_PROFILE,
    MAX_BRANCH_EXPENSES,
    SUPPORTED_CAPABILITIES,
    CallerContext,
    Rejected,
    canonicalize_intent,
    execute_command,
    init_isolated_store,
    lookup_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "backend" / "tests" / "fixtures" / "p1_expense_create_candidate.json"
INTERRUPTED = 86


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_bytes().decode("utf-8"))


def _read_tables(db_path: Path) -> dict:
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        branches = [dict(r) for r in db.execute("SELECT * FROM branches")]
        revisions = [dict(r) for r in db.execute("SELECT * FROM revisions ORDER BY seq")]
        expenses = [dict(r) for r in db.execute("SELECT * FROM expenses ORDER BY entity_id")]
        receipts = [
            dict(r) for r in db.execute("SELECT * FROM operation_receipts ORDER BY operation_id")
        ]
        outbox = [dict(r) for r in db.execute("SELECT * FROM outbox ORDER BY operation_id")]
    return {
        "integrity": [tuple(row) for row in integrity],
        "branches": branches,
        "revisions": revisions,
        "expenses": expenses,
        "receipts": receipts,
        "outbox": outbox,
    }


class ExpenseExecutorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="P2 expense ")
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        self.db_path = self.folder / "isolated.sqlite"
        init_isolated_store(self.db_path)
        self.caller = CallerContext.for_fixture()
        self.fixture = _load_fixture()
        self.command_bytes = json.dumps(self.fixture["command"], ensure_ascii=False).encode("utf-8")
        self.query_bytes = json.dumps(self.fixture["receipt_query"], ensure_ascii=False).encode(
            "utf-8"
        )

    def test_canonical_encoding_and_declared_capabilities(self):
        self.assertEqual(SUPPORTED_CAPABILITIES["canonical_profile"], CANONICAL_PROFILE)
        self.assertEqual(SUPPORTED_CAPABILITIES["commands"], ("expense.create",))
        self.assertEqual(SUPPORTED_CAPABILITIES["currencies"], ("CNY",))

        _, encoded, digest = canonicalize_intent(self.command_bytes, "control")
        reordered = json.dumps(self.fixture["command"], ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
        self.assertEqual(canonicalize_intent(reordered, "control")[1:], (encoded, digest))

        query_val, _, query_digest = canonicalize_intent(self.query_bytes, "receipt-query")
        self.assertEqual(query_val["operation"], self.fixture["command"]["operation"])
        self.assertNotEqual(query_digest, digest)

        mutated = json.loads(self.command_bytes.decode("utf-8"))
        mutated["payload"]["allow_shared_receipts"] = True
        self.assertNotEqual(
            canonicalize_intent(json.dumps(mutated).encode("utf-8"), "control")[2], digest
        )

    def test_interrupt_before_commit_leaves_zero_business_writes_and_retry_succeeds_once(self):
        cmd_file = self.folder / "cmd.json"
        cmd_file.write_bytes(self.command_bytes)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "desktop.expense_executor",
                "execute",
                "--database",
                str(self.db_path),
                "--input",
                str(cmd_file),
                "--fault",
                "before_commit",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, INTERRUPTED, proc.stderr)
        self.assertEqual(proc.stdout, b"")

        # Separate connection verifies no partial state was committed.
        state = _read_tables(self.db_path)
        self.assertEqual(state["integrity"], [("ok",)])
        self.assertEqual([r["revision_id"] for r in state["revisions"]], ["fixture-r0"])
        self.assertEqual(state["branches"][0]["head_revision_id"], "fixture-r0")
        self.assertEqual(state["expenses"], [])
        self.assertEqual(state["receipts"], [])
        self.assertEqual(state["outbox"], [])

        # Lookup reports not_found before retry.
        looked = lookup_receipt(self.db_path, self.query_bytes, caller=self.caller)
        self.assertEqual(looked, {"ok": True, "state": "not_found", "receipt": None})

        # Retrying the same command succeeds exactly once.
        retried = execute_command(self.db_path, self.command_bytes, caller=self.caller)
        self.assertTrue(retried["ok"])
        self.assertFalse(retried["replayed"])
        rev_id = retried["receipt"]["result_revision_id"]
        self.assertNotEqual(rev_id, "fixture-r0")

        after = _read_tables(self.db_path)
        self.assertEqual([r["revision_id"] for r in after["revisions"]], ["fixture-r0", rev_id])
        self.assertEqual(len(after["expenses"]), 1)
        self.assertEqual(after["expenses"][0]["entity_id"], "fixture-lunch")
        self.assertEqual(after["expenses"][0]["amount_cents"], 8635)
        self.assertEqual(len(after["receipts"]), 1)
        self.assertEqual(len(after["outbox"]), 1)

    def test_interrupt_after_commit_before_reply_lookup_and_replay_even_after_head_advances(self):
        cmd_file = self.folder / "cmd.json"
        query_file = self.folder / "query.json"
        cmd_file.write_bytes(self.command_bytes)
        query_file.write_bytes(self.query_bytes)

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "desktop.expense_executor",
                "execute",
                "--database",
                str(self.db_path),
                "--input",
                str(cmd_file),
                "--fault",
                "after_commit_before_reply",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, INTERRUPTED, proc.stderr)
        self.assertEqual(proc.stdout, b"")

        # Separate process inspects persisted tables directly.
        state = _read_tables(self.db_path)
        self.assertEqual(state["integrity"], [("ok",)])
        self.assertEqual(len(state["revisions"]), 2)
        rev_id = state["revisions"][1]["revision_id"]
        self.assertEqual(state["revisions"][1]["parent_revision_id"], "fixture-r0")
        self.assertEqual(state["branches"][0]["head_revision_id"], rev_id)
        self.assertEqual(len(state["expenses"]), 1)
        self.assertEqual(state["expenses"][0]["entity_id"], "fixture-lunch")
        self.assertEqual(state["expenses"][0]["amount_cents"], 8635)
        self.assertEqual(len(state["receipts"]), 1)
        self.assertEqual(state["receipts"][0]["result_revision_id"], rev_id)
        self.assertEqual(len(state["outbox"]), 1)
        self.assertEqual(state["outbox"][0]["result_revision_id"], rev_id)

        # Separate CLI process queries receipt after restart.
        lookup_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "desktop.expense_executor",
                "lookup",
                "--database",
                str(self.db_path),
                "--input",
                str(query_file),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(lookup_proc.returncode, 0, lookup_proc.stderr)
        looked = json.loads(lookup_proc.stdout)
        self.assertTrue(looked["ok"])
        self.assertEqual(looked["state"], "committed")
        self.assertEqual(looked["receipt"]["effect"], "changed")
        self.assertEqual(looked["receipt"]["result_revision_id"], rev_id)

        # Retry with reordered JSON keys returns original rev_id with zero extra rows.
        reordered = json.dumps(self.fixture["command"], ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
        replay = execute_command(self.db_path, reordered, caller=self.caller)
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["receipt"]["result_revision_id"], rev_id)
        self.assertEqual(len(_read_tables(self.db_path)["revisions"]), 2)
        self.assertEqual(len(_read_tables(self.db_path)["expenses"]), 1)
        self.assertEqual(len(_read_tables(self.db_path)["outbox"]), 1)

        # Advance branch head to rev2 via a second distinct expense.
        second_cmd = json.loads(self.command_bytes.decode("utf-8"))
        second_cmd["operation"]["operation_id"] = "fixture-op-dinner"
        second_cmd["target"]["entity_id"] = "fixture-dinner"
        second_cmd["expected_revision_id"] = rev_id
        second_cmd["payload"]["title"] = "晚餐"
        second_cmd["payload"]["amount_cents"] = 4200
        second = execute_command(
            self.db_path, json.dumps(second_cmd).encode("utf-8"), caller=self.caller
        )
        self.assertTrue(second["ok"])
        rev2 = second["receipt"]["result_revision_id"]
        self.assertNotEqual(rev2, rev_id)

        # Replaying the original operation after branch head advanced still returns original rev_id.
        late_replay = execute_command(self.db_path, self.command_bytes, caller=self.caller)
        self.assertTrue(late_replay["ok"])
        self.assertTrue(late_replay["replayed"])
        self.assertEqual(late_replay["receipt"]["result_revision_id"], rev_id)
        final_state = _read_tables(self.db_path)
        self.assertEqual(final_state["branches"][0]["head_revision_id"], rev2)
        self.assertEqual(len(final_state["revisions"]), 3)
        self.assertEqual(len(final_state["expenses"]), 2)
        self.assertEqual(len(final_state["receipts"]), 2)
        self.assertEqual(len(final_state["outbox"]), 2)

    def test_section_15_conflict_and_validation_rejections(self):
        first = execute_command(self.db_path, self.command_bytes, caller=self.caller)
        self.assertTrue(first["ok"])
        rev_id = first["receipt"]["result_revision_id"]

        # Same key, amount 8635 -> 8636 => intent_conflict.
        diff_amount = json.loads(self.command_bytes.decode("utf-8"))
        diff_amount["payload"]["amount_cents"] = 8636
        res_amount = execute_command(
            self.db_path, json.dumps(diff_amount).encode("utf-8"), caller=self.caller
        )
        self.assertEqual(res_amount, {"ok": False, "error": "intent_conflict"})

        # Same key, only allow_shared_receipts false -> true => intent_conflict.
        diff_control = json.loads(self.command_bytes.decode("utf-8"))
        diff_control["payload"]["allow_shared_receipts"] = True
        res_control = execute_command(
            self.db_path, json.dumps(diff_control).encode("utf-8"), caller=self.caller
        )
        self.assertEqual(res_control, {"ok": False, "error": "intent_conflict"})

        # New operation_id + new entity_id with stale expected_revision_id => baseline_conflict.
        stale_base = json.loads(self.command_bytes.decode("utf-8"))
        stale_base["operation"]["operation_id"] = "fixture-op-2"
        stale_base["target"]["entity_id"] = "fixture-lunch-2"
        res_base = execute_command(
            self.db_path, json.dumps(stale_base).encode("utf-8"), caller=self.caller
        )
        self.assertFalse(res_base["ok"])
        self.assertEqual(res_base["error"], "baseline_conflict")
        self.assertEqual(res_base["current_revision_id"], rev_id)

        # New operation_id on current head but existing entity_id => entity_exists.
        dup_entity = json.loads(self.command_bytes.decode("utf-8"))
        dup_entity["operation"]["operation_id"] = "fixture-op-3"
        dup_entity["expected_revision_id"] = rev_id
        res_entity = execute_command(
            self.db_path, json.dumps(dup_entity).encode("utf-8"), caller=self.caller
        )
        self.assertEqual(res_entity, {"ok": False, "error": "entity_exists"})

        # Invalid amounts: 0, true, 1.25, 10000000000 => rejected with zero writes.
        for bad_amount in (0, True, 1.25, 10_000_000_000):
            with self.subTest(bad_amount=bad_amount):
                bad_cmd = json.loads(self.command_bytes.decode("utf-8"))
                bad_cmd["operation"]["operation_id"] = f"fixture-op-bad-{bad_amount}"
                bad_cmd["target"]["entity_id"] = f"fixture-bad-{bad_amount}"
                bad_cmd["expected_revision_id"] = rev_id
                bad_cmd["payload"]["amount_cents"] = bad_amount
                res = execute_command(
                    self.db_path, json.dumps(bad_cmd).encode("utf-8"), caller=self.caller
                )
                self.assertFalse(res["ok"])

        # Unsupported capabilities (non-CNY, non-null paid_on, non-empty receipts, other kind).
        for label, mutate in (
            ("non_cny", lambda c: c["payload"].update(currency="USD")),
            ("non_null_paid_on", lambda c: c["payload"].update(paid_on="2026-11-23")),
            ("non_empty_receipts", lambda c: c["payload"].update(receipt_ids=["r1"])),
            ("unsupported_kind", lambda c: c.update(kind="expense.patch")),
        ):
            with self.subTest(capability=label):
                bad_cmd = json.loads(self.command_bytes.decode("utf-8"))
                bad_cmd["operation"]["operation_id"] = f"fixture-op-cap-{label}"
                bad_cmd["target"]["entity_id"] = f"fixture-cap-{label}"
                bad_cmd["expected_revision_id"] = rev_id
                mutate(bad_cmd)
                res = execute_command(
                    self.db_path, json.dumps(bad_cmd).encode("utf-8"), caller=self.caller
                )
                self.assertFalse(res["ok"])

        # Original expense and receipt remain untouched after all rejections.
        state = _read_tables(self.db_path)
        self.assertEqual(len(state["revisions"]), 2)
        self.assertEqual(len(state["expenses"]), 1)
        self.assertEqual(state["expenses"][0]["amount_cents"], 8635)
        self.assertEqual(len(state["receipts"]), 1)
        self.assertEqual(state["receipts"][0]["result_revision_id"], rev_id)

        with self.assertRaises(Rejected):
            canonicalize_intent(b'{"a":1,"a":2}', "control")

    def test_authorization_checked_before_receipt_lookup_and_execution(self):
        created = execute_command(self.db_path, self.command_bytes, caller=self.caller)
        self.assertTrue(created["ok"])

        wrong_device = CallerContext.for_fixture(device_id="other-device")
        wrong_dataset = CallerContext.for_fixture(dataset_id="other-dataset")
        no_lookup = CallerContext.for_fixture(can_lookup_receipt=False)
        no_write = CallerContext.for_fixture(can_write=False)

        for bad_caller in (wrong_device, wrong_dataset, no_lookup):
            res = lookup_receipt(self.db_path, self.query_bytes, caller=bad_caller)
            self.assertEqual(res, {"ok": False, "error": "unauthorized"})

        for bad_caller in (wrong_device, wrong_dataset, no_write):
            res = execute_command(self.db_path, self.command_bytes, caller=bad_caller)
            self.assertEqual(res, {"ok": False, "error": "unauthorized"})

    def test_concurrent_same_operation_subprocesses_never_duplicate_expense(self):
        cmd_file = self.folder / "concurrent-cmd.json"
        cmd_file.write_bytes(self.command_bytes)
        workers = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "desktop.expense_executor",
                    "execute",
                    "--database",
                    str(self.db_path),
                    "--input",
                    str(cmd_file),
                ],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(6)
        ]
        results = []
        for proc in workers:
            stdout, stderr = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, stderr)
            results.append(json.loads(stdout))

        rev_ids = {r["receipt"]["result_revision_id"] for r in results}
        self.assertEqual(len(rev_ids), 1)
        self.assertEqual(sum(1 for r in results if not r["replayed"]), 1)
        self.assertEqual(sum(1 for r in results if r["replayed"]), 5)

        state = _read_tables(self.db_path)
        self.assertEqual(state["integrity"], [("ok",)])
        self.assertEqual(len(state["revisions"]), 2)
        self.assertEqual(len(state["expenses"]), 1)
        self.assertEqual(state["expenses"][0]["amount_cents"], 8635)
        self.assertEqual(len(state["receipts"]), 1)
        self.assertEqual(len(state["outbox"]), 1)

    def test_init_execute_and_lookup_guard_unrelated_and_missing_databases(self):
        unrelated = self.folder / "unrelated.sqlite"
        with closing(sqlite3.connect(unrelated)) as db:
            db.execute("CREATE TABLE unrelated(x INTEGER)")
            db.execute("INSERT INTO unrelated VALUES (1)")
            db.commit()
        before_bytes = unrelated.read_bytes()

        with self.assertRaisesRegex(ValueError, "existing path"):
            init_isolated_store(unrelated)
        self.assertEqual(
            execute_command(unrelated, self.command_bytes, caller=self.caller),
            {
                "ok": False,
                "error": "invalid_store",
            },
        )
        self.assertEqual(
            lookup_receipt(unrelated, self.query_bytes, caller=self.caller),
            {
                "ok": False,
                "error": "invalid_store",
            },
        )

        # Unrelated DB must remain completely untouched: same bytes, 1 table, no WAL sidecars.
        self.assertEqual(unrelated.read_bytes(), before_bytes)
        self.assertFalse(Path(f"{unrelated}-wal").exists())
        self.assertFalse(Path(f"{unrelated}-shm").exists())
        with closing(sqlite3.connect(unrelated)) as db:
            tables = [
                row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            ]
            journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(tables, ["unrelated"])
        self.assertEqual(journal_mode, "delete")

        # Non-existent DB must be rejected without creating a file.
        missing = self.folder / "does-not-exist.sqlite"
        self.assertEqual(
            execute_command(missing, self.command_bytes, caller=self.caller),
            {
                "ok": False,
                "error": "invalid_store",
            },
        )
        self.assertEqual(
            lookup_receipt(missing, self.query_bytes, caller=self.caller),
            {
                "ok": False,
                "error": "invalid_store",
            },
        )
        self.assertFalse(missing.exists())

        # Re-initializing an already-initialized experimental store is also refused.
        with self.assertRaisesRegex(ValueError, "existing path"):
            init_isolated_store(self.db_path)

    def test_non_null_day_and_activity_id_are_rejected_without_writes(self):
        self.assertEqual(SUPPORTED_CAPABILITIES["day"], "null_only")
        self.assertEqual(SUPPORTED_CAPABILITIES["activity_id"], "null_only")

        for label, field_name, value in (
            ("day_60", "day", 60),
            ("unknown_activity", "activity_id", "unknown-stop"),
        ):
            with self.subTest(case=label):
                cmd = json.loads(self.command_bytes.decode("utf-8"))
                cmd["operation"]["operation_id"] = f"fixture-op-{label}"
                cmd["target"]["entity_id"] = f"fixture-{label}"
                cmd["payload"][field_name] = value
                res = execute_command(
                    self.db_path, json.dumps(cmd).encode("utf-8"), caller=self.caller
                )
                self.assertEqual(res, {"ok": False, "error": "unsupported_capability"})
                state = _read_tables(self.db_path)
                self.assertEqual([r["revision_id"] for r in state["revisions"]], ["fixture-r0"])
                self.assertEqual(state["receipts"], [])
                self.assertEqual(state["expenses"], [])
                self.assertEqual(state["outbox"], [])

    def test_branch_expense_limit_2000_rejects_new_and_allows_replay(self):
        self.assertEqual(SUPPORTED_CAPABILITIES["max_expenses_per_branch"], MAX_BRANCH_EXPENSES)
        first = execute_command(self.db_path, self.command_bytes, caller=self.caller)
        self.assertTrue(first["ok"])
        rev_id = first["receipt"]["result_revision_id"]

        # Seed 1999 additional expenses in the same branch to reach the 2000-expense cap.
        rows = [
            (
                "fixture-dataset",
                "fixture-travel",
                "fixture-branch",
                f"seed-expense-{idx}",
                rev_id,
                f"预置消费 {idx}",
                100,
                "CNY",
                None,
                "other",
                None,
                None,
                "",
                "[]",
                None,
                0,
                f"seed-op-{idx}",
            )
            for idx in range(MAX_BRANCH_EXPENSES - 1)
        ]
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executemany(
                "INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            db.commit()

        # A brand-new expense over the 2000 limit must be rejected with zero new writes.
        over_cmd = json.loads(self.command_bytes.decode("utf-8"))
        over_cmd["operation"]["operation_id"] = "fixture-op-over-limit"
        over_cmd["target"]["entity_id"] = "fixture-over-limit"
        over_cmd["expected_revision_id"] = rev_id
        over_res = execute_command(
            self.db_path, json.dumps(over_cmd).encode("utf-8"), caller=self.caller
        )
        self.assertEqual(over_res, {"ok": False, "error": "expense_limit_exceeded"})

        state_after_reject = _read_tables(self.db_path)
        self.assertEqual(len(state_after_reject["expenses"]), MAX_BRANCH_EXPENSES)
        self.assertEqual(len(state_after_reject["revisions"]), 2)
        self.assertEqual(len(state_after_reject["receipts"]), 1)
        self.assertEqual(len(state_after_reject["outbox"]), 1)

        # Replaying the original committed operation while at 2000 expenses still succeeds.
        replay = execute_command(self.db_path, self.command_bytes, caller=self.caller)
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["receipt"]["result_revision_id"], rev_id)

        state_after_replay = _read_tables(self.db_path)
        self.assertEqual(len(state_after_replay["expenses"]), MAX_BRANCH_EXPENSES)
        self.assertEqual(len(state_after_replay["revisions"]), 2)
        self.assertEqual(len(state_after_replay["receipts"]), 1)
        self.assertEqual(len(state_after_replay["outbox"]), 1)


if __name__ == "__main__":
    unittest.main()
