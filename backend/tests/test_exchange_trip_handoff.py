"""Independent-process end-to-end tests for the cross-repo 0.1.0 trip handoff CLI.

Note: Commands generated in this test are desktop stub sender inputs persisted via
desktop/expense_pending.py; real Android inputs and mobile confirmations are verified
separately via fixed mobile receipts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
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


def test_unrelated_sqlite_rejected_without_modification_across_all_three_entries(tmp_path: Path):
    """receive, lookup, and inspect-trip reject unrelated SQLite files without mutating them."""
    valid_work = tmp_path / "valid-handoff"
    init_proc = _run_handoff("init-trip", "--work-dir", str(valid_work))
    assert init_proc.returncode == 0
    ctx = json.loads(init_proc.stdout.decode("utf-8"))

    unrelated_db = tmp_path / "unrelated.sqlite"
    with closing(sqlite3.connect(unrelated_db)) as db:
        db.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)")
        db.execute("INSERT INTO unrelated VALUES (1, 'do-not-touch')")
        db.commit()

    before_sha256 = hashlib.sha256(unrelated_db.read_bytes()).hexdigest()

    cmd_bytes = _make_desktop_stub_command(
        dataset_id=ctx["dataset_id"],
        source_device_id=ctx["authorized_devices"][0],
        operation_id="op-unrelated-1",
        travel_id=ctx["travel_id"],
        branch_id=ctx["branch_id"],
        entity_id="unrelated-1",
        expected_revision_id=ctx["current_revision_id"],
    )
    query_bytes = json.dumps(
        {
            "profile": "p1-host-query-1",
            "kind": "receipt.lookup",
            "operation": {
                "dataset_id": ctx["dataset_id"],
                "source_device_id": ctx["authorized_devices"][0],
                "operation_id": "op-unrelated-1",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    recv_proc = _run_handoff(
        "receive",
        "--database",
        str(unrelated_db),
        "--scope",
        ctx["scope_config"],
        "--input",
        "-",
        input_bytes=cmd_bytes,
    )
    assert recv_proc.returncode == 1
    assert json.loads(recv_proc.stdout.decode("utf-8")) == {
        "ok": False,
        "error": "invalid_handoff_database",
    }

    look_proc = _run_handoff(
        "lookup",
        "--database",
        str(unrelated_db),
        "--scope",
        ctx["scope_config"],
        "--input",
        "-",
        input_bytes=query_bytes,
    )
    assert look_proc.returncode == 1
    assert json.loads(look_proc.stdout.decode("utf-8")) == {
        "ok": False,
        "error": "invalid_handoff_database",
    }

    insp_proc = _run_handoff(
        "inspect-trip",
        "--database",
        str(unrelated_db),
        "--branch-id",
        ctx["branch_id"],
    )
    assert insp_proc.returncode == 1
    assert json.loads(insp_proc.stdout.decode("utf-8")) == {
        "ok": False,
        "error": "invalid_handoff_database",
    }

    # Verify unrelated DB bytes, table list, and absence of WAL/SHM/journal companions.
    assert hashlib.sha256(unrelated_db.read_bytes()).hexdigest() == before_sha256
    for suffix in ("-wal", "-shm", "-journal"):
        assert not os.path.lexists(f"{unrelated_db}{suffix}")
    with closing(sqlite3.connect(f"{unrelated_db.as_uri()}?mode=ro", uri=True)) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
        assert tables == ["unrelated"]


def test_scope_strict_boolean_and_explicit_permissions_for_read_and_write(tmp_path: Path):
    """Malformed/missing/false permissions in scope.json must fail closed on both new & old keys."""
    work_dir = tmp_path / "perm-handoff"
    init_proc = _run_handoff("init-trip", "--work-dir", str(work_dir))
    assert init_proc.returncode == 0
    ctx = json.loads(init_proc.stdout.decode("utf-8"))
    device_id = ctx["authorized_devices"][0]

    # Commit one legitimate expense first so we can test already-committed receipt leak prevention.
    committed_op = "op-perm-committed-1"
    committed_cmd = _make_desktop_stub_command(
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id=committed_op,
        travel_id=ctx["travel_id"],
        branch_id=ctx["branch_id"],
        entity_id="perm-e1",
        expected_revision_id=ctx["current_revision_id"],
    )
    ok_proc = _run_handoff(
        "receive",
        "--database",
        ctx["database"],
        "--scope",
        ctx["scope_config"],
        "--input",
        "-",
        input_bytes=committed_cmd,
    )
    assert ok_proc.returncode == 0
    committed_v = json.loads(ok_proc.stdout.decode("utf-8"))["receipt"]["result_revision_id"]

    new_cmd = _make_desktop_stub_command(
        dataset_id=ctx["dataset_id"],
        source_device_id=device_id,
        operation_id="op-perm-new-2",
        travel_id=ctx["travel_id"],
        branch_id=ctx["branch_id"],
        entity_id="perm-e2",
        expected_revision_id=committed_v,
    )
    committed_query = json.dumps(
        {
            "profile": "p1-host-query-1",
            "kind": "receipt.lookup",
            "operation": {
                "dataset_id": ctx["dataset_id"],
                "source_device_id": device_id,
                "operation_id": committed_op,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    base_scope = json.loads(Path(ctx["scope_config"]).read_text(encoding="utf-8"))

    # 1. String "false" for can_write must be rejected as invalid_scope_config (never truthy!).
    str_write_scope = tmp_path / "scope-str-write.json"
    str_write_scope.write_text(json.dumps({**base_scope, "can_write": "false"}), encoding="utf-8")
    for payload in (new_cmd, committed_cmd):
        proc = _run_handoff(
            "receive",
            "--database",
            ctx["database"],
            "--scope",
            str(str_write_scope),
            "--input",
            "-",
            input_bytes=payload,
        )
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode("utf-8"))
        assert out == {"ok": False, "error": "invalid_scope_config"}
        assert "receipt" not in out

    # 2. Missing can_write or can_lookup_receipt must be rejected as invalid_scope_config.
    missing_write_scope = tmp_path / "scope-missing-write.json"
    missing_write_doc = {k: v for k, v in base_scope.items() if k != "can_write"}
    missing_write_scope.write_text(json.dumps(missing_write_doc), encoding="utf-8")
    proc_mw = _run_handoff(
        "receive",
        "--database",
        ctx["database"],
        "--scope",
        str(missing_write_scope),
        "--input",
        "-",
        input_bytes=new_cmd,
    )
    assert proc_mw.returncode == 1
    assert json.loads(proc_mw.stdout.decode("utf-8")) == {
        "ok": False,
        "error": "invalid_scope_config",
    }

    str_lookup_scope = tmp_path / "scope-str-lookup.json"
    str_lookup_scope.write_text(
        json.dumps({**base_scope, "can_lookup_receipt": "false"}), encoding="utf-8"
    )
    proc_sl = _run_handoff(
        "lookup",
        "--database",
        ctx["database"],
        "--scope",
        str(str_lookup_scope),
        "--input",
        "-",
        input_bytes=committed_query,
    )
    assert proc_sl.returncode == 1
    out_sl = json.loads(proc_sl.stdout.decode("utf-8"))
    assert out_sl == {"ok": False, "error": "invalid_scope_config"}
    assert "receipt" not in out_sl

    # 3. Explicit boolean can_write=False blocks writes & replays (no receipt leak), lookup allowed.
    read_only_scope = tmp_path / "scope-ro.json"
    read_only_scope.write_text(
        json.dumps({**base_scope, "can_write": False, "can_lookup_receipt": True}),
        encoding="utf-8",
    )
    for payload in (new_cmd, committed_cmd):
        proc = _run_handoff(
            "receive",
            "--database",
            ctx["database"],
            "--scope",
            str(read_only_scope),
            "--input",
            "-",
            input_bytes=payload,
        )
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode("utf-8"))
        assert out == {"ok": False, "error": "unauthorized"}
        assert "receipt" not in out

    ro_lookup = _run_handoff(
        "lookup",
        "--database",
        ctx["database"],
        "--scope",
        str(read_only_scope),
        "--input",
        "-",
        input_bytes=committed_query,
    )
    assert ro_lookup.returncode == 0
    assert json.loads(ro_lookup.stdout.decode("utf-8"))["state"] == "committed"

    # 4. Explicit boolean can_lookup_receipt=False blocks lookup on committed receipt (no leak).
    no_lookup_scope = tmp_path / "scope-no-lookup.json"
    no_lookup_scope.write_text(
        json.dumps({**base_scope, "can_write": True, "can_lookup_receipt": False}),
        encoding="utf-8",
    )
    nl_proc = _run_handoff(
        "lookup",
        "--database",
        ctx["database"],
        "--scope",
        str(no_lookup_scope),
        "--input",
        "-",
        input_bytes=committed_query,
    )
    assert nl_proc.returncode == 1
    out_nl = json.loads(nl_proc.stdout.decode("utf-8"))
    assert out_nl == {"ok": False, "error": "unauthorized"}
    assert "receipt" not in out_nl

    # Verify zero extra writes occurred during all rejected calls.
    insp_proc = _run_handoff(
        "inspect-trip",
        "--database",
        ctx["database"],
        "--branch-id",
        ctx["branch_id"],
    )
    assert insp_proc.returncode == 0
    insp = json.loads(insp_proc.stdout.decode("utf-8"))
    assert insp["head_revision_id"] == committed_v
    assert len(insp["receipts"]) == 1


def test_init_trip_context_out_exclusive_creation_and_reserved_path_guards(tmp_path: Path):
    """init-trip must refuse existing files, symlinks, and reserved paths for --context-out."""
    # 1. Existing regular file at --context-out must be rejected and left untouched.
    existing_ctx = tmp_path / "existing-context.json"
    existing_ctx.write_text('{"keep": "original"}\n', encoding="utf-8")
    work1 = tmp_path / "work-existing-ctx"
    p1 = _run_handoff("init-trip", "--work-dir", str(work1), "--context-out", str(existing_ctx))
    assert p1.returncode == 1
    assert existing_ctx.read_text(encoding="utf-8") == '{"keep": "original"}\n'
    assert not (work1 / "travel.sqlite").exists()
    assert not (work1 / "scope.json").exists()

    # 2. Symlink at --context-out (to existing file and dangling) must be rejected.
    target_file = tmp_path / "symlink-target.json"
    target_file.write_text('{"secret": true}\n', encoding="utf-8")
    link_path = tmp_path / "ctx-symlink.json"
    os.symlink(target_file, link_path)
    work2 = tmp_path / "work-symlink-ctx"
    p2 = _run_handoff("init-trip", "--work-dir", str(work2), "--context-out", str(link_path))
    assert p2.returncode == 1
    assert target_file.read_text(encoding="utf-8") == '{"secret": true}\n'
    assert not (work2 / "travel.sqlite").exists()
    assert not (work2 / "scope.json").exists()

    dangling_link = tmp_path / "ctx-dangling.json"
    os.symlink(tmp_path / "nonexistent-target.json", dangling_link)
    work3 = tmp_path / "work-dangling-ctx"
    p3 = _run_handoff("init-trip", "--work-dir", str(work3), "--context-out", str(dangling_link))
    assert p3.returncode == 1
    assert not (tmp_path / "nonexistent-target.json").exists()
    assert not (work3 / "travel.sqlite").exists()
    assert not (work3 / "scope.json").exists()

    # 3. Reserved handoff paths inside work_dir must be rejected without publishing artifacts.
    for reserved_name in (
        "travel.sqlite",
        "scope.json",
        "travel.sqlite-wal",
        "travel.sqlite-shm",
        "travel.sqlite-journal",
    ):
        work_res = tmp_path / f"work-reserved-{reserved_name}"
        p_res = _run_handoff(
            "init-trip",
            "--work-dir",
            str(work_res),
            "--context-out",
            str(work_res / reserved_name),
        )
        assert p_res.returncode == 1, reserved_name
        assert not (work_res / "travel.sqlite").exists()
        assert not (work_res / "scope.json").exists()
