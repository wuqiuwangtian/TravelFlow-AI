"""End-to-end HTTPS tests: paired LAN server + 0.1.0 expense.create & receipt.lookup."""

from __future__ import annotations

import http.client
import json
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFF_CLI = REPO_ROOT / "backend" / "scripts" / "exchange_trip_handoff.py"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from desktop.expense_patch_candidate import (  # noqa: E402
    PatchScope,
    apply_desktop_patch,
    init_mobile_replica,
)
from desktop.expense_pending import (  # noqa: E402
    consume_delivery_response,
    consume_lookup_response,
    init_sender_store,
    prepare_pending_delivery,
    read_pending_operation,
    stage_pending_operation,
)
from desktop.lan_pairing import (  # noqa: E402
    ForegroundPairingServer,
    _control_call,
    compute_sas_code,
    pinned_https_request,
)


def _init_handoff_trip(work_dir: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(HANDOFF_CLI), "init-trip", "--work-dir", str(work_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    return json.loads(proc.stdout.decode("utf-8"))


def _inspect_handoff_trip(db_path: str, branch_id: str, fork_branch_id: str) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(HANDOFF_CLI),
            "inspect-trip",
            "--database",
            db_path,
            "--branch-id",
            branch_id,
            "--fork-branch-id",
            fork_branch_id,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    return json.loads(proc.stdout.decode("utf-8"))


def _pair_mobile_device(
    state_dir: Path, host: str, port: int, pin: str, *, paired_device_id: str
) -> str:
    st_open, sess = _control_call(state_dir, "POST", "/control/pairing/open", {"ttl_seconds": 120})
    assert st_open == 200
    nonce = secrets.token_hex(16)
    st_req, req = pinned_https_request(
        host,
        port,
        "POST",
        "/pair/request",
        expected_cert_sha256=pin,
        body={
            "pairing_session_id": sess["pairing_session_id"],
            "pairing_code": sess["pairing_code"],
            "device_id": paired_device_id,
            "device_name": "Android HTTPS Test",
            "client_nonce": nonce,
        },
    )
    assert st_req == 200
    sas = compute_sas_code(
        pairing_code=sess["pairing_code"],
        cert_sha256=pin,
        pairing_session_id=sess["pairing_session_id"],
        device_id=paired_device_id,
        client_nonce=nonce,
        server_nonce=req["server_nonce"],
    )
    st_app, _ = _control_call(
        state_dir,
        "POST",
        "/control/pairing/approve",
        {"request_id": req["request_id"], "sas_code": sas},
    )
    assert st_app == 200
    st_done, done = pinned_https_request(
        host,
        port,
        "POST",
        "/pair/complete",
        expected_cert_sha256=pin,
        body={
            "request_id": req["request_id"],
            "device_id": paired_device_id,
            "client_nonce": nonce,
            "sas_code": sas,
        },
    )
    assert st_done == 200
    return str(done["device_token"])


def _make_create_bytes(
    *,
    dataset_id: str,
    source_device_id: str,
    operation_id: str,
    travel_id: str,
    branch_id: str,
    entity_id: str,
    expected_revision_id: str,
    amount_cents: int = 1723,
    title: str = "机场快轨",
) -> bytes:
    return json.dumps(
        {
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
                "category": "transport",
                "day": None,
                "activity_id": None,
                "notes": "HTTPS 实连测试",
                "receipt_ids": [],
                "allow_shared_receipts": False,
                "budget_item_id": None,
                "settles_budget": False,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _make_lookup_bytes(*, dataset_id: str, source_device_id: str, operation_id: str) -> bytes:
    return json.dumps(
        {
            "profile": "p1-host-query-1",
            "kind": "receipt.lookup",
            "operation": {
                "dataset_id": dataset_id,
                "source_device_id": source_device_id,
                "operation_id": operation_id,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_paired_https_expense_create_reply_loss_replay_lookup_and_revocation(tmp_path: Path):
    """Verify full pinned-HTTPS create, reply loss, replay, lookup, spoofing, and revocation."""
    trip_work = tmp_path / "trip-work"
    state_dir = tmp_path / "pairing-state"
    sender_db = tmp_path / "mobile_sender.sqlite"
    init_sender_store(sender_db)

    ctx = _init_handoff_trip(trip_work)
    trip_db = Path(ctx["database"])
    paired_id = "android-paired-1"
    biz_device_id = "android-business-1"

    srv = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0, trip_db=trip_db)
    info = srv.start()
    try:
        host, port, pin = info["lan_host"], info["lan_port"], info["cert_sha256"]

        # 1. Pair device over pinned HTTPS + loopback control approval.
        token = _pair_mobile_device(state_dir, host, port, pin, paired_device_id=paired_id)
        auth_headers = {"Authorization": f"Bearer {token}", "X-Device-Id": paired_id}

        # Stage persisted 0.1.0 command in sender SQLite store.
        op_id = "op-https-metro-1"
        raw_cmd = _make_create_bytes(
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id=op_id,
            travel_id=ctx["travel_id"],
            branch_id=ctx["branch_id"],
            entity_id="https-metro-1",
            expected_revision_id=ctx["current_revision_id"],
            amount_cents=1723,
        )
        staged = stage_pending_operation(sender_db, raw_cmd)
        assert staged["ok"] is True
        expected_sha = staged["record"]["intent_sha256"]

        prep1 = prepare_pending_delivery(
            sender_db,
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id=op_id,
        )
        assert prep1["ok"] is True and not prep1["already_confirmed"]
        persisted_bytes = prep1["raw_bytes"]

        lookup_bytes = _make_lookup_bytes(
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id=op_id,
        )

        # 2. Paired but NOT yet bound to a trip grant -> 403 exchange_not_authorized, zero writes.
        st_unb, unb = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-create",
            expected_cert_sha256=pin,
            raw_body=persisted_bytes,
            headers=auth_headers,
        )
        assert st_unb == 403 and unb["error"] == "exchange_not_authorized"

        st_unb_l, unb_l = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/receipt-lookup",
            expected_cert_sha256=pin,
            raw_body=lookup_bytes,
            headers=auth_headers,
        )
        assert st_unb_l == 403 and unb_l["error"] == "exchange_not_authorized"

        # 3. Desktop binds paired device to authorized business identity & primary branch.
        st_bind, bind_res = _control_call(
            state_dir,
            "POST",
            "/control/devices/bind-exchange",
            {
                "device_id": paired_id,
                "dataset_id": ctx["dataset_id"],
                "source_device_id": biz_device_id,
                "travel_id": ctx["travel_id"],
                "branch_id": ctx["branch_id"],
                "can_write": True,
                "can_lookup_receipt": True,
            },
        )
        assert st_bind == 200 and bind_res["ok"] is True

        # 4. Arm one-shot reply loss after commit -> first HTTPS delivery drops without reply.
        st_fault, _ = _control_call(
            state_dir,
            "POST",
            "/control/exchange/fault",
            {"fault": "after_commit_before_reply"},
        )
        assert st_fault == 200

        with pytest.raises((http.client.RemoteDisconnected, ConnectionError, OSError)):
            pinned_https_request(
                host,
                port,
                "POST",
                "/exchange/expense-create",
                expected_cert_sha256=pin,
                raw_body=persisted_bytes,
                headers=auth_headers,
            )

        consume_delivery_response(
            sender_db,
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id=op_id,
            receiver_raw={"transport_error": "reply_lost"},
            transport_error="reply_lost",
        )
        after_loss = read_pending_operation(
            sender_db,
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id=op_id,
        )
        assert after_loss["record"]["delivery_state"] == "verification_pending"
        assert after_loss["record"]["confirmed_revision_id"] is None

        # 5. Redeliver exact persisted bytes over pinned HTTPS -> replayed=True, single entry!
        st_rep, rep_res = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-create",
            expected_cert_sha256=pin,
            raw_body=persisted_bytes,
            headers=auth_headers,
        )
        assert st_rep == 200
        assert rep_res["ok"] is True
        assert rep_res["replayed"] is True
        assert rep_res["state"] == "committed"
        assert rep_res["receipt"]["intent_sha256"] == expected_sha
        v_committed = rep_res["receipt"]["result_revision_id"]
        assert v_committed and v_committed != ctx["current_revision_id"]

        conf = consume_delivery_response(
            sender_db,
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id=op_id,
            receiver_raw=rep_res,
        )
        assert conf["ok"] is True
        assert conf["record"]["delivery_state"] == "confirmed"
        assert conf["record"]["confirmed_revision_id"] == v_committed

        # 6. Independent receipt.lookup over pinned HTTPS returns identical committed receipt.
        st_lk, lk_res = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/receipt-lookup",
            expected_cert_sha256=pin,
            raw_body=lookup_bytes,
            headers=auth_headers,
        )
        assert st_lk == 200
        assert lk_res["ok"] is True
        assert lk_res["state"] == "committed"
        assert lk_res["receipt"]["result_revision_id"] == v_committed
        assert lk_res["receipt"]["intent_sha256"] == expected_sha

        reconciled = consume_lookup_response(
            sender_db,
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id=op_id,
            receiver_raw=lk_res,
        )
        assert reconciled["ok"] is True
        assert reconciled["record"]["confirmed_revision_id"] == v_committed

        # 7. Spoofed source_device_id or misdirected fork branch -> 403 unauthorized, zero writes.
        spoof_cmd = _make_create_bytes(
            dataset_id=ctx["dataset_id"],
            source_device_id="spoofed-other-device",
            operation_id="op-spoof-1",
            travel_id=ctx["travel_id"],
            branch_id=ctx["branch_id"],
            entity_id="spoof-e1",
            expected_revision_id=v_committed,
        )
        st_sp, sp_res = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-create",
            expected_cert_sha256=pin,
            raw_body=spoof_cmd,
            headers=auth_headers,
        )
        assert st_sp == 403 and sp_res == {"ok": False, "error": "unauthorized"}

        wrong_branch_cmd = _make_create_bytes(
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id="op-wrong-branch-1",
            travel_id=ctx["travel_id"],
            branch_id=ctx["fork_branch_id"],
            entity_id="wrong-b1",
            expected_revision_id=ctx["fork_revision_id"],
        )
        st_wb, wb_res = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-create",
            expected_cert_sha256=pin,
            raw_body=wrong_branch_cmd,
            headers=auth_headers,
        )
        assert st_wb == 403 and wb_res == {"ok": False, "error": "unauthorized"}

        # 8. Revoke device -> both create and lookup immediately return 403 device_revoked.
        st_rev, rev_ctrl = _control_call(
            state_dir, "POST", "/control/devices/revoke", {"device_id": paired_id}
        )
        assert st_rev == 200 and rev_ctrl["revoked"] is True

        new_cmd_after_rev = _make_create_bytes(
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_device_id,
            operation_id="op-after-revoke-2",
            travel_id=ctx["travel_id"],
            branch_id=ctx["branch_id"],
            entity_id="after-revoke-2",
            expected_revision_id=v_committed,
        )
        st_rc, rc_res = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-create",
            expected_cert_sha256=pin,
            raw_body=new_cmd_after_rev,
            headers=auth_headers,
        )
        assert st_rc == 403 and rc_res == {"ok": False, "error": "device_revoked"}

        st_rl, rl_res = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/receipt-lookup",
            expected_cert_sha256=pin,
            raw_body=lookup_bytes,
            headers=auth_headers,
        )
        assert st_rl == 403 and rl_res == {"ok": False, "error": "device_revoked"}
        assert "receipt" not in rl_res

        replacement = _pair_mobile_device(state_dir, host, port, pin, paired_device_id=paired_id)
        fresh_headers = {"Authorization": f"Bearer {replacement}", "X-Device-Id": paired_id}
        for route, raw in (
            ("/exchange/receipt-lookup", lookup_bytes),
            ("/exchange/expense-create", new_cmd_after_rev),
        ):
            status, result = pinned_https_request(
                host,
                port,
                "POST",
                route,
                expected_cert_sha256=pin,
                raw_body=raw,
                headers=fresh_headers,
            )
            assert status == 403 and result == {"ok": False, "error": "exchange_not_authorized"}
    finally:
        srv.stop()

    # 9. Inspect real desktop trip DB after server stop: single 1723-cent addition, fork intact.
    insp = _inspect_handoff_trip(ctx["database"], ctx["branch_id"], ctx["fork_branch_id"])
    assert insp["version_ids"] == [
        "409854fb9585",
        "42ac255b7c33",
        "9639db1eb3b8",
        v_committed,
    ]
    assert insp["head_paid_cents"] == 8635 + 1723
    assert [(e["id"], e["amount_cents"]) for e in insp["head_expenses"]] == [
        ("d1-fictional-lunch", 8635),
        ("https-metro-1", 1723),
    ]
    assert insp["fork"]["head_expense_count"] == 0
    assert len(insp["receipts"]) == 1
    assert insp["receipts"][0]["operation_id"] == op_id
    assert insp["receipts"][0]["intent_sha256"] == expected_sha


def test_paired_https_reverse_patch_pull_reply_loss_confirm_auth_and_restart(tmp_path: Path):
    """Paired authorized mobile pulls its own expense.patch, survives reply loss, and confirms."""
    trip_work = tmp_path / "trip-reverse"
    state_dir = tmp_path / "pairing-reverse"
    ctx = _init_handoff_trip(trip_work)
    trip_db = Path(ctx["database"])

    paired_a = "android-paired-A"
    biz_a = "android-business-A"
    paired_b = "android-paired-B"
    biz_b = "android-business-B"
    paired_unbound = "android-paired-unbound"

    srv = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0, trip_db=trip_db)
    info = srv.start()
    try:
        host, port, pin = info["lan_host"], info["lan_port"], info["cert_sha256"]
        tok_a = _pair_mobile_device(state_dir, host, port, pin, paired_device_id=paired_a)
        tok_b = _pair_mobile_device(state_dir, host, port, pin, paired_device_id=paired_b)
        tok_u = _pair_mobile_device(state_dir, host, port, pin, paired_device_id=paired_unbound)
        hdr_a = {"Authorization": f"Bearer {tok_a}", "X-Device-Id": paired_a}
        hdr_b = {"Authorization": f"Bearer {tok_b}", "X-Device-Id": paired_b}
        hdr_u = {"Authorization": f"Bearer {tok_u}", "X-Device-Id": paired_unbound}

        # Bind A to (dataset, biz_a, travel, primary_branch) and B to biz_b (different creator).
        for dev_id, src_id in ((paired_a, biz_a), (paired_b, biz_b)):
            st_b, _ = _control_call(
                state_dir,
                "POST",
                "/control/devices/bind-exchange",
                {
                    "device_id": dev_id,
                    "dataset_id": ctx["dataset_id"],
                    "source_device_id": src_id,
                    "travel_id": ctx["travel_id"],
                    "branch_id": ctx["branch_id"],
                    "can_write": True,
                    "can_lookup_receipt": True,
                },
            )
            assert st_b == 200

        # 1. Device A creates 1723-cent expense over pinned HTTPS.
        create_raw = _make_create_bytes(
            dataset_id=ctx["dataset_id"],
            source_device_id=biz_a,
            operation_id="op-rev-create-1",
            travel_id=ctx["travel_id"],
            branch_id=ctx["branch_id"],
            entity_id="https-metro-1",
            expected_revision_id=ctx["current_revision_id"],
            amount_cents=1723,
        )
        st_c, res_c = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-create",
            expected_cert_sha256=pin,
            raw_body=create_raw,
            headers=hdr_a,
        )
        assert st_c == 200 and res_c["ok"] is True
        v_created = res_c["receipt"]["result_revision_id"]

        # 2. Desktop edits ONLY amount (17.23 -> 21.50) via existing PUT endpoint.
        put_script = (
            "import asyncio, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path('backend').resolve()))\n"
            "sys.path.insert(0, str(Path('backend/tests').resolve()))\n"
            "from test_exchange_receive_trip import _open_isolated_app, _close_isolated_app\n"
            "async def run():\n"
            "  conn, store, cp_cm, graph, client = await _open_isolated_app(Path(sys.argv[1]))\n"
            "  try:\n"
            "    r = await client.put(f'/api/versions/{sys.argv[2]}/expenses/https-metro-1', "
            "json={'title':'机场快轨','amount':'21.50','category':'transport',"
            "'notes':'HTTPS 实连测试'})\n"
            "    assert r.status_code == 200, r.text\n"
            "    print(r.json()['version']['id'])\n"
            "  finally:\n"
            "    await _close_isolated_app(conn, cp_cm, client)\n"
            "asyncio.run(run())\n"
        )
        put_proc = subprocess.run(
            [sys.executable, "-c", put_script, ctx["database"], v_created],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert put_proc.returncode == 0, put_proc.stderr.decode("utf-8", errors="replace")
        v_patched = put_proc.stdout.decode("utf-8").strip()

        # 3. Unbound device U gets 403; Device B (different creator) sees 0 items.
        st_u, res_u = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/pull",
            expected_cert_sha256=pin,
            body={},
            headers=hdr_u,
        )
        assert st_u == 403 and res_u["error"] == "exchange_not_authorized"

        st_b_pull, res_b_pull = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/pull",
            expected_cert_sha256=pin,
            body={},
            headers=hdr_b,
        )
        assert st_b_pull == 200 and res_b_pull == {"ok": True, "items": []}

        # 4. Arm reply-loss after pull prepare -> first pull drops after marking
        #    verification_pending; retry pulls exact same raw_command_json bytes!
        st_flt, _ = _control_call(
            state_dir,
            "POST",
            "/control/exchange/fault",
            {"fault": "after_pull_prepare_before_reply"},
        )
        assert st_flt == 200

        with pytest.raises((http.client.RemoteDisconnected, ConnectionError, OSError)):
            pinned_https_request(
                host,
                port,
                "POST",
                "/exchange/expense-patch/pull",
                expected_cert_sha256=pin,
                body={},
                headers=hdr_a,
            )

        st_p1, pull1 = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/pull",
            expected_cert_sha256=pin,
            body={},
            headers=hdr_a,
        )
        assert st_p1 == 200 and len(pull1["items"]) == 1
        item1 = pull1["items"][0]
        assert item1["delivery_state"] == "verification_pending"
        assert item1["expected_revision_id"] == v_created
        assert item1["source_revision_id"] == v_patched

        st_p2, pull2 = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/pull",
            expected_cert_sha256=pin,
            body={},
            headers=hdr_a,
        )
        assert st_p2 == 200 and pull2["items"][0]["raw_command_json"] == item1["raw_command_json"]

        # 5. Apply pulled raw bytes on mobile replica to obtain a real complete mobile receipt.
        mobile_db = tmp_path / "mobile_reverse.sqlite"
        init_mobile_replica(
            mobile_db,
            defaults={
                "dataset_id": ctx["dataset_id"],
                "travel_id": ctx["travel_id"],
                "branch_id": ctx["branch_id"],
                "entity_id": "https-metro-1",
                "initial_local_revision_id": "m-rev-1",
                "initial_synced_desktop_revision_id": v_created,
                "initial_expense": {
                    "id": "https-metro-1",
                    "title": "机场快轨",
                    "amount_cents": 1723,
                    "currency": "CNY",
                    "paid_on": None,
                    "category": "transport",
                    "notes": "HTTPS 实连测试",
                    "updated_at": "2026-11-24T12:00:00Z",
                },
            },
        )
        mobile_res = apply_desktop_patch(
            mobile_db,
            item1["raw_command_json"].encode("utf-8"),
            scope=PatchScope(
                dataset_id=ctx["dataset_id"],
                authorized_devices=frozenset({"desktop-device-1"}),
                authorized_targets=frozenset({(ctx["travel_id"], ctx["branch_id"])}),
            ),
        )
        assert mobile_res["ok"] is True

        # Device B (wrong creator device) cannot confirm Device A's patch -> 403 unauthorized.
        st_b_cf, res_b_cf = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/confirm",
            expected_cert_sha256=pin,
            body=mobile_res,
            headers=hdr_b,
        )
        assert st_b_cf == 403 and res_b_cf["error"] == "unauthorized"

        # Tampered receipt from Device A -> 400 receipt_mismatch, stays verification_pending.
        tampered = {
            **mobile_res,
            "receipt": {**mobile_res["receipt"], "intent_sha256": "0" * 64},
        }
        st_bad_cf, res_bad_cf = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/confirm",
            expected_cert_sha256=pin,
            body=tampered,
            headers=hdr_a,
        )
        assert st_bad_cf == 400 and res_bad_cf["error"] == "receipt_mismatch"

        # Valid receipt from Device A confirms atomically.
        st_ok_cf, res_ok_cf = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/confirm",
            expected_cert_sha256=pin,
            body=mobile_res,
            headers=hdr_a,
        )
        assert st_ok_cf == 200
        assert res_ok_cf["confirmed"] is True
        assert res_ok_cf["record"]["delivery_state"] == "confirmed"
        assert res_ok_cf["record"]["confirmed_mobile_revision_id"] == "m-rev-2"

        # Duplicate confirmation is idempotent and adds zero desktop versions.
        st_dup_cf, res_dup_cf = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-patch/confirm",
            expected_cert_sha256=pin,
            body=mobile_res,
            headers=hdr_a,
        )
        assert st_dup_cf == 200 and res_dup_cf["already_confirmed"] is True
    finally:
        srv.stop()

    # 6. Restart desktop server: pending outbound is 0; revoked Device A gets 403 on pull & confirm.
    srv2 = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0, trip_db=trip_db)
    info2 = srv2.start()
    try:
        st_empty, empty_pull = pinned_https_request(
            info2["lan_host"],
            info2["lan_port"],
            "POST",
            "/exchange/expense-patch/pull",
            expected_cert_sha256=info2["cert_sha256"],
            body={},
            headers=hdr_a,
        )
        assert st_empty == 200 and empty_pull == {"ok": True, "items": []}

        st_rev, _ = _control_call(
            state_dir, "POST", "/control/devices/revoke", {"device_id": paired_a}
        )
        assert st_rev == 200

        st_rp, res_rp = pinned_https_request(
            info2["lan_host"],
            info2["lan_port"],
            "POST",
            "/exchange/expense-patch/pull",
            expected_cert_sha256=info2["cert_sha256"],
            body={},
            headers=hdr_a,
        )
        assert st_rp == 403 and res_rp == {"ok": False, "error": "device_revoked"}

        st_rcf, res_rcf = pinned_https_request(
            info2["lan_host"],
            info2["lan_port"],
            "POST",
            "/exchange/expense-patch/confirm",
            expected_cert_sha256=info2["cert_sha256"],
            body=mobile_res,
            headers=hdr_a,
        )
        assert st_rcf == 403 and res_rcf == {"ok": False, "error": "device_revoked"}
    finally:
        srv2.stop()

    insp = _inspect_handoff_trip(ctx["database"], ctx["branch_id"], ctx["fork_branch_id"])
    assert insp["pending_outbound_count"] == 0
    assert insp["head_revision_id"] == v_patched
    assert insp["head_paid_cents"] == 8635 + 2150
    assert len(insp["outbound_operations"]) == 1
    assert insp["outbound_operations"][0]["delivery_state"] == "confirmed"
    assert insp["outbound_operations"][0]["confirmed_mobile_revision_id"] == "m-rev-2"
    assert insp["fork"]["head_expense_count"] == 0
