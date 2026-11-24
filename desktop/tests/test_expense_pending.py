"""Cross-process sender-side pending-intent persistence and recovery acceptance tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import desktop.expense_pending as expense_pending
from desktop.expense_executor import (
    CallerContext,
    canonicalize_intent,
    execute_command,
    init_isolated_store,
)
from desktop.expense_pending import (
    init_sender_store,
    read_pending_operation,
    reconcile_pending_operation,
    send_pending_operation,
    stage_pending_operation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "backend" / "tests" / "fixtures" / "p1_expense_create_candidate.json"


def _run_sender_cli(*args: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-m", "desktop.expense_pending", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    payload = json.loads(proc.stdout)
    return proc.returncode, payload


def _read_receiver_summary(receiver_db: Path) -> dict:
    with closing(sqlite3.connect(receiver_db)) as db:
        db.row_factory = sqlite3.Row
        expenses = [
            dict(r) for r in db.execute("SELECT entity_id, amount_cents, paid_on FROM expenses")
        ]
        revisions = [dict(r) for r in db.execute("SELECT revision_id, seq FROM revisions")]
        receipts = [
            dict(r)
            for r in db.execute(
                "SELECT operation_id, intent_sha256, result_revision_id FROM operation_receipts"
            )
        ]
        outbox = [dict(r) for r in db.execute("SELECT operation_id, state FROM outbox")]
    return {
        "expenses": expenses,
        "revisions": revisions,
        "receipts": receipts,
        "outbox": outbox,
    }


class ExpensePendingRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="P2 pending ")
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        self.sender_db = self.folder / "sender.sqlite"
        self.receiver_db = self.folder / "receiver.sqlite"
        init_sender_store(self.sender_db)
        init_isolated_store(self.receiver_db)
        self.caller = CallerContext.for_fixture()
        self.fixture = json.loads(FIXTURE.read_bytes().decode("utf-8"))
        self.op_id = self.fixture["command"]["operation"]["operation_id"]

    def test_capability_mismatch_paid_on_persists_full_intent_across_sender_restart(self):
        cmd = json.loads(json.dumps(self.fixture["command"]))
        cmd["payload"]["paid_on"] = "2026-11-23"
        raw_bytes = json.dumps(cmd, ensure_ascii=False).encode("utf-8")
        _, expected_canonical, expected_sha = canonicalize_intent(raw_bytes, "control")

        cmd_file = self.folder / "paid_on_cmd.json"
        cmd_file.write_bytes(raw_bytes)

        # Process 1: stage before sending, then attempt delivery to current desktop peer.
        rc_stage, staged = _run_sender_cli(
            "stage", "--sender-db", str(self.sender_db), "--input", str(cmd_file)
        )
        self.assertEqual(rc_stage, 0)
        self.assertEqual(staged["record"]["delivery_state"], "unsent")

        rc_send, sent = _run_sender_cli(
            "send",
            "--sender-db",
            str(self.sender_db),
            "--receiver-db",
            str(self.receiver_db),
            "--operation-id",
            self.op_id,
        )
        self.assertEqual(rc_send, 1)
        self.assertEqual(sent["error"], "unsupported_capability")
        self.assertEqual(sent["receiver_raw"], {"ok": False, "error": "unsupported_capability"})

        checked = reconcile_pending_operation(
            self.sender_db, self.receiver_db, caller=self.caller, operation_id=self.op_id
        )
        self.assertEqual(checked["receiver_raw"]["state"], "not_found_at_settled_read")
        self.assertEqual(checked["error"], "unsupported_capability")

        # Receiver has zero writes.
        receiver = _read_receiver_summary(self.receiver_db)
        self.assertEqual(receiver["expenses"], [])
        self.assertEqual(receiver["receipts"], [])
        self.assertEqual(receiver["outbox"], [])

        # Process 2 (fresh sender restart): full original intent & baseline remain intact.
        rc_inspect, inspected = _run_sender_cli(
            "inspect", "--sender-db", str(self.sender_db), "--operation-id", self.op_id
        )
        self.assertEqual(rc_inspect, 0)
        rec = inspected["record"]
        self.assertEqual(rec["delivery_state"], "unsent")
        self.assertEqual(rec["last_error"], "unsupported_capability")
        self.assertIsNone(rec["confirmed_revision_id"])
        self.assertEqual(rec["expected_revision_id"], "fixture-r0")
        self.assertEqual(rec["operation"]["operation_id"], self.op_id)
        self.assertEqual(rec["raw_input_json"].encode("utf-8"), raw_bytes)
        self.assertEqual(rec["canonical_intent_json"].encode("utf-8"), expected_canonical)
        self.assertEqual(rec["intent_sha256"], expected_sha)
        self.assertEqual(json.loads(rec["raw_input_json"])["payload"]["paid_on"], "2026-11-23")

    def test_write_lock_and_settled_not_found_recover_across_restarts_with_single_entry(self):
        raw_bytes = json.dumps(self.fixture["command"], ensure_ascii=False).encode("utf-8")
        cmd_file = self.folder / "lunch.json"
        cmd_file.write_bytes(raw_bytes)

        rc_stage, _ = _run_sender_cli(
            "stage", "--sender-db", str(self.sender_db), "--input", str(cmd_file)
        )
        self.assertEqual(rc_stage, 0)

        # Hold an active SQLite write lock on receiver DB while sender attempts send + reconcile.
        with closing(
            sqlite3.connect(str(self.receiver_db), timeout=5.0, isolation_level=None)
        ) as lock_conn:
            lock_conn.execute("BEGIN IMMEDIATE")
            try:
                rc_send, sent = _run_sender_cli(
                    "send",
                    "--sender-db",
                    str(self.sender_db),
                    "--receiver-db",
                    str(self.receiver_db),
                    "--operation-id",
                    self.op_id,
                )
                self.assertEqual(rc_send, 1)
                self.assertEqual(sent["error"], "storage_busy")
                self.assertEqual(sent["record"]["delivery_state"], "verification_pending")

                rc_rec, rec = _run_sender_cli(
                    "reconcile",
                    "--sender-db",
                    str(self.sender_db),
                    "--receiver-db",
                    str(self.receiver_db),
                    "--operation-id",
                    self.op_id,
                )
                self.assertEqual(rc_rec, 1)
                self.assertEqual(rec["error"], "storage_busy")
                self.assertEqual(
                    rec["receiver_raw"],
                    {"ok": True, "state": "unknown", "reason": "storage_busy"},
                )
                self.assertEqual(rec["record"]["delivery_state"], "verification_pending")
            finally:
                lock_conn.execute("ROLLBACK")

        # Fresh sender process after lock release: settled lookup reports not_found_at_settled_read.
        rc_settled, settled = _run_sender_cli(
            "reconcile",
            "--sender-db",
            str(self.sender_db),
            "--receiver-db",
            str(self.receiver_db),
            "--operation-id",
            self.op_id,
        )
        self.assertEqual(rc_settled, 1)
        self.assertEqual(settled["error"], "not_found_at_settled_read")
        self.assertEqual(settled["record"]["delivery_state"], "verification_pending")
        self.assertEqual(_read_receiver_summary(self.receiver_db)["expenses"], [])

        # Fresh sender process retries with original key -> commits once and confirms.
        rc_retry, retried = _run_sender_cli(
            "send",
            "--sender-db",
            str(self.sender_db),
            "--receiver-db",
            str(self.receiver_db),
            "--operation-id",
            self.op_id,
        )
        self.assertEqual(rc_retry, 0)
        self.assertEqual(retried["record"]["delivery_state"], "confirmed")
        rev_id = retried["record"]["confirmed_revision_id"]
        self.assertTrue(rev_id)

        receiver = _read_receiver_summary(self.receiver_db)
        self.assertEqual(
            receiver["expenses"],
            [{"entity_id": "fixture-lunch", "amount_cents": 8635, "paid_on": None}],
        )
        self.assertEqual(len(receiver["receipts"]), 1)
        self.assertEqual(receiver["receipts"][0]["result_revision_id"], rev_id)

        # Subsequent send in a new process stays confirmed and never duplicates on receiver.
        rc_again, again = _run_sender_cli(
            "send",
            "--sender-db",
            str(self.sender_db),
            "--receiver-db",
            str(self.receiver_db),
            "--operation-id",
            self.op_id,
        )
        self.assertEqual(rc_again, 0)
        self.assertEqual(again["record"]["confirmed_revision_id"], rev_id)
        self.assertEqual(len(_read_receiver_summary(self.receiver_db)["expenses"]), 1)

    def test_reply_lost_after_commit_recovers_across_restarts_by_reconcile_and_by_retry(self):
        for mode in ("reconcile", "retry_send"):
            with self.subTest(recovery_mode=mode):
                s_db = self.folder / f"sender-{mode}.sqlite"
                r_db = self.folder / f"receiver-{mode}.sqlite"
                init_sender_store(s_db)
                init_isolated_store(r_db)

                raw_bytes = json.dumps(self.fixture["command"], ensure_ascii=False).encode("utf-8")
                cmd_file = self.folder / f"cmd-{mode}.json"
                cmd_file.write_bytes(raw_bytes)

                rc_stage, _ = _run_sender_cli(
                    "stage", "--sender-db", str(s_db), "--input", str(cmd_file)
                )
                self.assertEqual(rc_stage, 0)

                # Receiver commits and aborts before replying.
                rc_send, sent = _run_sender_cli(
                    "send",
                    "--sender-db",
                    str(s_db),
                    "--receiver-db",
                    str(r_db),
                    "--operation-id",
                    self.op_id,
                    "--fault",
                    "after_commit_before_reply",
                )
                self.assertEqual(rc_send, 1)
                self.assertEqual(sent["error"], "reply_lost")
                self.assertEqual(sent["record"]["delivery_state"], "verification_pending")
                self.assertIsNone(sent["record"]["confirmed_revision_id"])

                # Receiver already committed exactly one expense.
                mid_receiver = _read_receiver_summary(r_db)
                self.assertEqual(len(mid_receiver["expenses"]), 1)
                committed_rev = mid_receiver["receipts"][0]["result_revision_id"]

                # Fresh sender process inspects persisted pending state before recovery.
                rc_insp, inspected = _run_sender_cli(
                    "inspect", "--sender-db", str(s_db), "--operation-id", self.op_id
                )
                self.assertEqual(rc_insp, 0)
                self.assertEqual(inspected["record"]["delivery_state"], "verification_pending")

                # Fresh sender process recovers either via lookup reconcile or original-key retry.
                action = "reconcile" if mode == "reconcile" else "send"
                rc_rec, recovered = _run_sender_cli(
                    action,
                    "--sender-db",
                    str(s_db),
                    "--receiver-db",
                    str(r_db),
                    "--operation-id",
                    self.op_id,
                )
                self.assertEqual(rc_rec, 0)
                self.assertEqual(recovered["record"]["delivery_state"], "confirmed")
                self.assertEqual(recovered["record"]["confirmed_revision_id"], committed_rev)
                self.assertEqual(recovered["record"]["confirmed_effect"], "changed")

                # Receiver still has exactly one expense and one receipt.
                final_receiver = _read_receiver_summary(r_db)
                self.assertEqual(len(final_receiver["expenses"]), 1)
                self.assertEqual(final_receiver["expenses"][0]["amount_cents"], 8635)
                self.assertEqual(len(final_receiver["receipts"]), 1)
                self.assertEqual(len(final_receiver["outbox"]), 1)

    def test_same_key_different_amount_or_control_never_falsely_confirms(self):
        for label, mutate in (
            ("different_amount_8636", lambda c: c["payload"].update(amount_cents=8636)),
            (
                "different_allow_shared_receipts",
                lambda c: c["payload"].update(allow_shared_receipts=True),
            ),
        ):
            with self.subTest(conflict=label):
                s_db = self.folder / f"sender-{label}.sqlite"
                r_db = self.folder / f"receiver-{label}.sqlite"
                init_sender_store(s_db)
                init_isolated_store(r_db)

                # Receiver already committed a different intent under the exact same operation key.
                peer_cmd = json.loads(json.dumps(self.fixture["command"]))
                mutate(peer_cmd)
                peer_res = execute_command(
                    r_db,
                    json.dumps(peer_cmd, ensure_ascii=False).encode("utf-8"),
                    caller=self.caller,
                )
                self.assertTrue(peer_res["ok"])

                # Sender stages original lunch (8635, allow_shared_receipts=false).
                orig_raw = json.dumps(self.fixture["command"], ensure_ascii=False).encode("utf-8")
                staged = stage_pending_operation(s_db, orig_raw)
                self.assertTrue(staged["ok"])
                orig_sha = staged["record"]["intent_sha256"]

                # Reconcile sees a different intent_sha256 -> must NOT confirm.
                rec = reconcile_pending_operation(
                    s_db, r_db, caller=self.caller, operation_id=self.op_id
                )
                self.assertFalse(rec["ok"])
                self.assertEqual(rec["error"], "intent_conflict")
                self.assertEqual(rec["record"]["delivery_state"], "verification_pending")
                self.assertIsNone(rec["record"]["confirmed_revision_id"])
                self.assertEqual(rec["record"]["intent_sha256"], orig_sha)

                # Sending also gets intent_conflict and keeps original pending intent intact.
                sent = send_pending_operation(
                    s_db, r_db, caller=self.caller, operation_id=self.op_id
                )
                self.assertFalse(sent["ok"])
                self.assertEqual(sent["error"], "intent_conflict")
                self.assertEqual(sent["record"]["delivery_state"], "verification_pending")
                self.assertIsNone(sent["record"]["confirmed_revision_id"])

                # Fresh read confirms sender still holds intact original 8635/false intent.
                reloaded = read_pending_operation(
                    s_db,
                    dataset_id=self.caller.dataset_id,
                    source_device_id=self.caller.device_id,
                    operation_id=self.op_id,
                )
                self.assertTrue(reloaded["ok"])
                self.assertEqual(reloaded["record"]["delivery_state"], "verification_pending")
                self.assertEqual(reloaded["record"]["raw_input_json"].encode("utf-8"), orig_raw)
                self.assertEqual(reloaded["record"]["intent_sha256"], orig_sha)

    def test_sender_store_refuses_unrelated_or_existing_database(self):
        unrelated = self.folder / "unrelated.sqlite"
        with closing(sqlite3.connect(unrelated)) as db:
            db.execute("CREATE TABLE unrelated(x INTEGER)")
            db.commit()
        before = unrelated.read_bytes()
        with self.assertRaisesRegex(ValueError, "existing path"):
            init_sender_store(unrelated)
        self.assertEqual(
            read_pending_operation(
                unrelated,
                dataset_id="fixture-dataset",
                source_device_id="fixture-device",
                operation_id=self.op_id,
            ),
            {"ok": False, "error": "invalid_sender_store"},
        )
        self.assertEqual(unrelated.read_bytes(), before)

    def test_late_stale_reconcile_never_downgrades_confirmed_state(self):
        raw_bytes = json.dumps(self.fixture["command"], ensure_ascii=False).encode("utf-8")
        self.assertTrue(stage_pending_operation(self.sender_db, raw_bytes)["ok"])

        lookup_done = threading.Event()
        allow_stale_finish = threading.Event()
        real_lookup = expense_pending.lookup_receipt
        stale_result: list[dict] = []

        def delayed_lookup(db_path, raw_query, *, caller):
            result = real_lookup(db_path, raw_query, caller=caller)
            self.assertEqual(result["state"], "not_found_at_settled_read")
            lookup_done.set()
            assert allow_stale_finish.wait(timeout=10)
            return result

        def run_stale_reconcile():
            stale_result.append(
                reconcile_pending_operation(
                    self.sender_db,
                    self.receiver_db,
                    caller=self.caller,
                    operation_id=self.op_id,
                )
            )

        with patch.object(expense_pending, "lookup_receipt", delayed_lookup):
            worker = threading.Thread(target=run_stale_reconcile)
            worker.start()
            self.assertTrue(lookup_done.wait(timeout=10))

            # Meanwhile, another caller sends, commits on receiver, and confirms sender.
            sent = send_pending_operation(
                self.sender_db,
                self.receiver_db,
                caller=self.caller,
                operation_id=self.op_id,
            )
            self.assertTrue(sent["ok"])
            self.assertEqual(sent["record"]["delivery_state"], "confirmed")
            committed_rev = sent["record"]["confirmed_revision_id"]
            self.assertTrue(committed_rev)

            # Release the stale not_found_at_settled_read reconcile.
            allow_stale_finish.set()
            worker.join(timeout=10)

        self.assertEqual(len(stale_result), 1)
        self.assertTrue(stale_result[0]["ok"])
        self.assertEqual(stale_result[0]["record"]["delivery_state"], "confirmed")
        self.assertEqual(stale_result[0]["record"]["confirmed_revision_id"], committed_rev)

        # Reopen sender DB in a fresh process: confirmed state & revision must survive intact.
        rc_insp, reopened = _run_sender_cli(
            "inspect", "--sender-db", str(self.sender_db), "--operation-id", self.op_id
        )
        self.assertEqual(rc_insp, 0)
        self.assertEqual(reopened["record"]["delivery_state"], "confirmed")
        self.assertEqual(reopened["record"]["confirmed_revision_id"], committed_rev)
        self.assertIsNone(reopened["record"]["last_error"])

        receiver = _read_receiver_summary(self.receiver_db)
        self.assertEqual(len(receiver["expenses"]), 1)
        self.assertEqual(len(receiver["receipts"]), 1)
        self.assertEqual(len(receiver["outbox"]), 1)

    def test_fault_path_preserves_business_rejection_and_caller_permissions(self):
        # Case 1: explicit paid_on rejected before commit even with after_commit_before_reply fault.
        cmd_paid = json.loads(json.dumps(self.fixture["command"]))
        cmd_paid["payload"]["paid_on"] = "2026-11-23"
        raw_paid = json.dumps(cmd_paid, ensure_ascii=False).encode("utf-8")
        self.assertTrue(stage_pending_operation(self.sender_db, raw_paid)["ok"])

        res_paid = send_pending_operation(
            self.sender_db,
            self.receiver_db,
            caller=self.caller,
            operation_id=self.op_id,
            fault="after_commit_before_reply",
        )
        self.assertFalse(res_paid["ok"])
        self.assertEqual(res_paid["error"], "unsupported_capability")
        self.assertEqual(res_paid["receiver_raw"], {"ok": False, "error": "unsupported_capability"})
        self.assertEqual(res_paid["record"]["delivery_state"], "unsent")
        self.assertEqual(res_paid["record"]["last_error"], "unsupported_capability")
        self.assertEqual(res_paid["record"]["raw_input_json"].encode("utf-8"), raw_paid)

        receiver_after_paid = _read_receiver_summary(self.receiver_db)
        self.assertEqual(receiver_after_paid["expenses"], [])
        self.assertEqual(receiver_after_paid["receipts"], [])
        self.assertEqual(receiver_after_paid["outbox"], [])

        # Case 2: caller with can_write=False under fault injection must be rejected unauthorized.
        s_db2 = self.folder / "sender-nowrite.sqlite"
        r_db2 = self.folder / "receiver-nowrite.sqlite"
        init_sender_store(s_db2)
        init_isolated_store(r_db2)
        raw_lunch = json.dumps(self.fixture["command"], ensure_ascii=False).encode("utf-8")
        self.assertTrue(stage_pending_operation(s_db2, raw_lunch)["ok"])

        readonly_caller = CallerContext.for_fixture(can_write=False)
        res_unauth = send_pending_operation(
            s_db2,
            r_db2,
            caller=readonly_caller,
            operation_id=self.op_id,
            fault="after_commit_before_reply",
        )
        self.assertFalse(res_unauth["ok"])
        self.assertEqual(res_unauth["error"], "unauthorized")
        self.assertEqual(res_unauth["receiver_raw"], {"ok": False, "error": "unauthorized"})
        self.assertEqual(res_unauth["record"]["delivery_state"], "verification_pending")
        self.assertEqual(res_unauth["record"]["last_error"], "unauthorized")
        self.assertIsNone(res_unauth["record"]["confirmed_revision_id"])

        receiver_after_unauth = _read_receiver_summary(r_db2)
        self.assertEqual(receiver_after_unauth["expenses"], [])
        self.assertEqual(receiver_after_unauth["receipts"], [])
        self.assertEqual(receiver_after_unauth["outbox"], [])


if __name__ == "__main__":
    unittest.main()
