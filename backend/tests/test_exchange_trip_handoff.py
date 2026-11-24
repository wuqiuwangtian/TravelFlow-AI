"""Independent-process end-to-end tests for the cross-repo 0.1.0 trip handoff CLI.

Note: Commands generated in this test are desktop stub sender inputs persisted via
desktop/expense_pending.py; real Android inputs and mobile confirmations are verified
separately via fixed mobile receipts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFF_CLI = REPO_ROOT / "backend" / "scripts" / "exchange_trip_handoff.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from desktop.expense_pending import (  # noqa: E402
    consume_delivery_response,
    consume_lookup_response,
    init_sender_store,
    prepare_pending_delivery,
    read_pending_operation,
    stage_pending_operation,
)


def _run_handoff(
    *args: str, input_bytes: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(HANDOFF_CLI), *args],
        cwd=REPO_ROOT,
        input=input_bytes,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _make_desktop_stub_command(
    *,
    dataset_id: str,
    source_device_id: str,
    operation_id: str,
    travel_id: str,
    branch_id: str,
    entity_id: str,
    expected_revision_id: str,
    title: str = "地铁交通卡充值",
    amount_cents: int = 3500,
    category: str = "transport",
) -> bytes:
    """Build a desktop-stub 0.1.0 expense.create command distinct from the fixed 86.35 lunch."""
    doc = {
        "profile": "p1-host-control-1",
        "kind": "expense.create",
        "operation": {
            "dataset_id": dataset_id,
            "source_device_id": source_device_id,
            "operation_id": operation_id,
        },
        "target": {
            "travel_id": travel_id,
            "branch_id": branch_id,
            "entity_id": entity_id,
        },
        "expected_revision_id": expected_revision_id,
        "payload": {
            "title": title,
            "amount_cents": amount_cents,
            "currency": "CNY",
            "paid_on": None,
            "category": category,
            "day": None,
            "activity_id": None,
            "notes": "桌面替身发送方隔离交接样例",
            "receipt_ids": [],
            "allow_shared_receipts": False,
            "budget_item_id": None,
            "settles_budget": False,
        },
    }
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def test_independent_process_handoff_reply_lost_retry_and_ledger_readback(tmp_path: Path):
    """Full subprocess sequence: init-trip -> fault reply loss -> reopen & replay -> verify."""
    work_dir = tmp_path / "handoff-trip"
    context_file = tmp_path / "context.json"
    sender_db = tmp_path / "desktop_stub_sender.sqlite"
    init_sender_store(sender_db)

    # 1. Subprocess: initialize isolated fictional trip copy from D1 fixture.
    init_proc = _run_handoff(
        "init-trip",
        "--work-dir",
        str(work_dir),
        "--context-out",
        str(context_file),
    )
    assert init_proc.returncode == 0, init_proc.stderr.decode("utf-8", errors="replace")
    ctx = json.loads(init_proc.stdout.decode("utf-8"))
    assert ctx["travel_id"] == "92ba6a148a17"
    assert ctx["branch_id"] == "f4f921d01f4e"
    assert ctx["current_revision_id"] == "9639db1eb3b8"
    assert ctx["fork_branch_id"] == "80a581ac74f9"
    assert ctx["fork_revision_id"] == "ce99e5ec7352"
    assert context_file.is_file()

    # Refusing to overwrite an existing handoff work dir.
    init_again = _run_handoff("init-trip", "--work-dir", str(work_dir))
    assert init_again.returncode == 1

    # 2. Desktop stub sender persists a new valid 0.1.0 command (35.00 CNY metro card, distinct
    #    from the fixed 86.35 CNY lunch) in its own isolated SQLite store.
    op_id = "op-handoff-metro-1"
    device_id = ctx["authorized_devices"][0]
    raw_cmd = _make_desktop_stub_command(
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=op_id,
        travel_id=ctx["travel_id"],
        branch_id=ctx["branch_id"],
        entity_id="metro-card-1",
        expected_revision_id=ctx["current_revision_id"],
        title="地铁交通卡充值",
        amount_cents=3500,
        category="transport",
    )
    staged = stage_pending_operation(sender_db, raw_cmd)
    assert staged["ok"] is True
    expected_sha256 = staged["record"]["intent_sha256"]

    # Export exact persisted sender bytes to a handoff file for file-based delivery.
    prep1 = prepare_pending_delivery(
        sender_db,
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=op_id,
    )
    assert prep1["ok"] is True and not prep1["already_confirmed"]
    cmd_file = tmp_path / "persisted-command.json"
    cmd_file.write_bytes(prep1["raw_bytes"])

    # 3. Subprocess: deliver from file with fault=after_commit_before_reply (simulates crash
    #    after atomic SQLite commit and before reply / checkpoint sync).
    drop_proc = _run_handoff(
        "receive",
        "--database",
        ctx["database"],
        "--scope",
        ctx["scope_config"],
        "--input",
        str(cmd_file),
        "--fault",
        "after_commit_before_reply",
    )
    assert drop_proc.returncode == 86
    assert drop_proc.stdout == b""

    after_drop = consume_delivery_response(
        sender_db,
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=op_id,
        receiver_raw={"exit_code": drop_proc.returncode, "stdout_empty": True},
        transport_error="reply_lost",
    )
    assert after_drop["ok"] is False
    assert after_drop["record"]["delivery_state"] == "verification_pending"
    assert after_drop["record"]["confirmed_revision_id"] is None

    # 4. Reopen sender DB, read exact same persisted bytes, and deliver via stdin in a new process.
    prep2 = prepare_pending_delivery(
        sender_db,
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=op_id,
    )
    assert prep2["ok"] is True and not prep2["already_confirmed"]
    assert prep2["raw_bytes"] == cmd_file.read_bytes()

    replay_proc = _run_handoff(
        "receive",
        "--database",
        ctx["database"],
        "--scope",
        ctx["scope_config"],
        "--input",
        "-",
        input_bytes=prep2["raw_bytes"],
    )
    assert replay_proc.returncode == 0, replay_proc.stderr.decode("utf-8", errors="replace")
    replay_raw = json.loads(replay_proc.stdout.decode("utf-8"))
    assert replay_raw["ok"] is True
    assert replay_raw["replayed"] is True
    assert replay_raw["state"] == "committed"
    receipt = replay_raw["receipt"]
    assert receipt["operation"] == {
        "dataset_id": ctx["dataset_id"],
        "source_device_id": device_id,
        "operation_id": op_id,
    }
    assert receipt["intent_sha256"] == expected_sha256
    assert receipt["entity_id"] == "metro-card-1"
    committed_revision_id = receipt["result_revision_id"]
    assert committed_revision_id and committed_revision_id != ctx["current_revision_id"]

    confirmed = consume_delivery_response(
        sender_db,
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=op_id,
        receiver_raw=replay_raw,
    )
    assert confirmed["ok"] is True
    assert confirmed["record"]["delivery_state"] == "confirmed"
    assert confirmed["record"]["confirmed_revision_id"] == committed_revision_id

    # 5. Subprocess: receipt.lookup via raw query bytes verifies durable receipt independently.
    query_bytes = json.dumps(
        {
            "profile": "p1-host-query-1",
            "kind": "receipt.lookup",
            "operation": {
                "dataset_id": ctx["dataset_id"],
                "source_device_id": device_id,
                "operation_id": op_id,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    lookup_proc = _run_handoff(
        "lookup",
        "--database",
        ctx["database"],
        "--scope",
        ctx["scope_config"],
        "--input",
        "-",
        input_bytes=query_bytes,
    )
    assert lookup_proc.returncode == 0, lookup_proc.stderr.decode("utf-8", errors="replace")
    lookup_raw = json.loads(lookup_proc.stdout.decode("utf-8"))
    assert lookup_raw["ok"] is True
    assert lookup_raw["state"] == "committed"
    assert lookup_raw["receipt"]["result_revision_id"] == committed_revision_id
    assert lookup_raw["receipt"]["intent_sha256"] == expected_sha256

    reconciled = consume_lookup_response(
        sender_db,
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=op_id,
        receiver_raw=lookup_raw,
    )
    assert reconciled["ok"] is True
    assert reconciled["record"]["delivery_state"] == "confirmed"
    assert reconciled["record"]["confirmed_revision_id"] == committed_revision_id

    # 6. Subprocess: unauthorized misdelivery to fork branch (using its real head) is rejected.
    wrong_branch_cmd = _make_desktop_stub_command(
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id="op-handoff-wrong-branch",
        travel_id=ctx["travel_id"],
        branch_id=ctx["fork_branch_id"],
        entity_id="wrong-branch-1",
        expected_revision_id=ctx["fork_revision_id"],
    )
    wrong_proc = _run_handoff(
        "receive",
        "--database",
        ctx["database"],
        "--scope",
        ctx["scope_config"],
        "--input",
        "-",
        input_bytes=wrong_branch_cmd,
    )
    assert wrong_proc.returncode == 1
    assert json.loads(wrong_proc.stdout.decode("utf-8")) == {"ok": False, "error": "unauthorized"}

    # 7. Subprocess: inspect desktop real trip ledger through existing FastAPI read endpoints.
    insp_proc = _run_handoff(
        "inspect-trip",
        "--database",
        ctx["database"],
        "--branch-id",
        ctx["branch_id"],
        "--fork-branch-id",
        ctx["fork_branch_id"],
    )
    assert insp_proc.returncode == 0, insp_proc.stderr.decode("utf-8", errors="replace")
    insp = json.loads(insp_proc.stdout.decode("utf-8"))
    assert insp["version_ids"] == [
        "409854fb9585",
        "42ac255b7c33",
        "9639db1eb3b8",
        committed_revision_id,
    ]
    assert insp["head_revision_id"] == committed_revision_id
    assert insp["head_checkpoint_id"] is not None
    assert insp["previous_expense_count"] == 1
    assert [(e["id"], e["amount_cents"]) for e in insp["head_expenses"]] == [
        ("d1-fictional-lunch", 8635),
        ("metro-card-1", 3500),
    ]
    assert insp["head_paid_cents"] == 12135
    assert "地铁交通卡充值" in insp["export_markdown"]
    assert "35.00" in insp["export_markdown"]
    assert insp["fork"] == {
        "branch_id": ctx["fork_branch_id"],
        "version_ids": [ctx["fork_revision_id"]],
        "head_expense_count": 0,
    }
    assert len(insp["receipts"]) == 1
    assert insp["receipts"][0]["operation_id"] == op_id
    assert insp["receipts"][0]["intent_sha256"] == expected_sha256
    assert insp["receipts"][0]["result_version_id"] == committed_revision_id
    assert insp["receipts"][0]["origin_direction"] == "received"

    # 8. Final fresh read of sender DB: operation key, intent digest, and confirmed revision match.
    final_sender = read_pending_operation(
        sender_db,
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=op_id,
    )
    assert final_sender["ok"] is True
    assert final_sender["record"]["delivery_state"] == "confirmed"
    assert final_sender["record"]["intent_sha256"] == expected_sha256
    assert final_sender["record"]["confirmed_revision_id"] == committed_revision_id
