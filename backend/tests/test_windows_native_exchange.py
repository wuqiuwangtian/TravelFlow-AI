"""End-to-end verification of Windows Native Python as the paired HTTPS business server.

Runs in two ways:
1. Directly on Windows Native Python 3.13+ (with backend deps installed):
     pytest -v backend/tests/test_windows_native_exchange.py
2. From WSL driving a real Windows Native Python 3.13+ foreground server over TCP/TLS:
     TRAVELFLOW_WINDOWS_BACKEND_PYTHON=/mnt/c/.../python.exe \\
     TRAVELFLOW_WINDOWS_BIND_HOST=10.226.92.198 \\
     (cd backend && .venv/bin/pytest -v tests/test_windows_native_exchange.py)
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from desktop.expense_patch_candidate import (  # noqa: E402
    PatchScope,
    apply_desktop_patch,
    init_mobile_replica,
)
from desktop.lan_pairing import compute_sas_code, pinned_https_request  # noqa: E402

WINDOWS_BACKEND_PYTHON = os.environ.get("TRAVELFLOW_WINDOWS_BACKEND_PYTHON")
IS_WINDOWS = os.name == "nt"


def _wsl_to_win(path: Path | str) -> str:
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


def _win_to_wsl(win_path: str) -> Path:
    return Path(subprocess.check_output(["wslpath", "-u", win_path], text=True).strip())


def _run_win_cli(
    win_python: str,
    args: list[str],
    *,
    cwd: Path = REPO_ROOT,
    timeout: float = 45.0,
) -> dict[str, Any]:
    proc = subprocess.run(
        [win_python, *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    assert proc.returncode == 0, (
        f"Windows CLI failed ({proc.returncode}): {args}\n{stdout}\n{stderr}"
    )
    return json.loads(stdout)


def _start_win_server(
    win_python: str,
    *,
    state_dir_arg: str,
    bind_host: str,
    trip_db_arg: str,
    open_pairing: bool,
    san_ips: list[str] | None = None,
) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    cmd = [
        win_python,
        "-m",
        "desktop.lan_pairing",
        "serve",
        "--state-dir",
        state_dir_arg,
        "--bind",
        bind_host,
        "--port",
        "0",
        "--trip-db",
        trip_db_arg,
    ]
    for ip in san_ips or []:
        cmd.extend(["--san-ip", ip])
    if open_pairing:
        cmd.append("--open-pairing")

    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    raw_line = proc.stdout.readline()
    if not raw_line:
        err = proc.stderr.read().decode("utf-8", errors="replace")
        proc.wait(timeout=5)
        raise AssertionError(f"Windows foreground server failed to start:\n{err}")
    info = json.loads(raw_line.decode("utf-8"))
    return proc, info


def _stop_win_server(win_python: str, proc: subprocess.Popen[bytes], *, state_dir_arg: str) -> None:
    try:
        if proc.poll() is None:
            res = _run_win_cli(
                win_python,
                ["-m", "desktop.lan_pairing", "stop", "--state-dir", state_dir_arg],
                timeout=20.0,
            )
            assert res.get("ok") is True and res.get("stopping") is True
            proc.wait(timeout=15.0)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5.0)


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
                "notes": "Windows 原生业务服务实测",
                "receipt_ids": [],
                "allow_shared_receipts": False,
                "budget_item_id": None,
                "settles_budget": False,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


@pytest.mark.skipif(
    not (IS_WINDOWS or WINDOWS_BACKEND_PYTHON),
    reason="Requires Windows Native Python or TRAVELFLOW_WINDOWS_BACKEND_PYTHON",
)
def test_windows_native_foreground_business_server_round_trip_and_restart(tmp_path: Path):
    """Verify Windows Native Python foreground server: create, edit, pull, confirm, and restart."""
    win_python = sys.executable if IS_WINDOWS else str(WINDOWS_BACKEND_PYTHON)
    assert Path(win_python).is_file(), f"Windows Python not found: {win_python}"

    # Verify the target interpreter is truly Windows Native (win32) Python 3.13+.
    env_info = _run_win_cli(
        win_python,
        [
            "-c",
            "import json, sys, tempfile; "
            "print(json.dumps({'platform': sys.platform, "
            "'version': list(sys.version_info[:3]), 'tempdir': tempfile.gettempdir()}))",
        ],
    )
    assert env_info["platform"] == "win32"
    assert tuple(env_info["version"][:2]) >= (3, 13)

    if IS_WINDOWS:
        bind_host = os.environ.get("TRAVELFLOW_WINDOWS_BIND_HOST", "127.0.0.1")
        work_local = tmp_path / "win-native-e2e"
        work_local.mkdir(parents=True, exist_ok=True)
        work_win = str(work_local)
        handoff_script = str(REPO_ROOT / "backend" / "scripts" / "exchange_trip_handoff.py")
        cleanup_dir: Path | None = None
    else:
        bind_host = os.environ.get("TRAVELFLOW_WINDOWS_BIND_HOST", "10.226.92.198")
        win_temp_wsl = _win_to_wsl(env_info["tempdir"])
        work_local = Path(tempfile.mkdtemp(prefix="travelflow-win-e2e-", dir=win_temp_wsl))
        work_win = _wsl_to_win(work_local)
        handoff_script = _wsl_to_win(REPO_ROOT / "backend" / "scripts" / "exchange_trip_handoff.py")
        cleanup_dir = work_local

    trip_dir_win = f"{work_win}\\trip"
    context_out_win = f"{work_win}\\context.json"
    state_dir_win = f"{work_win}\\pairing-state"
    paired_id = "android-win-paired-1"
    biz_id = "android-win-business-1"
    entity_id = "https-win-metro-1"

    try:
        # 1. Initialize isolated fictional trip DB on Windows NTFS via existing CLI.
        ctx = _run_win_cli(
            win_python,
            [
                handoff_script,
                "init-trip",
                "--work-dir",
                trip_dir_win,
                "--context-out",
                context_out_win,
            ],
        )
        trip_db_win = str(ctx["database"])
        assert (work_local / "context.json").is_file()

        # 2. Start Windows Native foreground HTTPS business server with --open-pairing.
        srv1, info1 = _start_win_server(
            win_python,
            state_dir_arg=state_dir_win,
            bind_host=bind_host,
            trip_db_arg=trip_db_win,
            open_pairing=True,
        )
        try:
            host = str(info1["lan_host"])
            port1 = int(info1["lan_port"])
            pin1 = str(info1["cert_sha256"])
            sess = info1["pairing_session"]
            assert host == bind_host
            assert len(pin1) == 64

            # Verify /health over pinned HTTPS reports exchange_enabled=True.
            st_h, health = pinned_https_request(
                host, port1, "GET", "/health", expected_cert_sha256=pin1, timeout=10.0
            )
            assert st_h == 200 and health["ok"] is True
            assert health["cert_sha256"] == pin1
            assert health["exchange_enabled"] is True

            # 3. Pair client over pinned HTTPS + Windows Native approve CLI.
            nonce = secrets.token_hex(16)
            st_req, req = pinned_https_request(
                host,
                port1,
                "POST",
                "/pair/request",
                expected_cert_sha256=pin1,
                body={
                    "pairing_session_id": sess["pairing_session_id"],
                    "pairing_code": sess["pairing_code"],
                    "device_id": paired_id,
                    "device_name": "Paired HTTPS Test Client",
                    "client_nonce": nonce,
                },
                timeout=10.0,
            )
            assert st_req == 200 and req["ok"] is True
            sas = compute_sas_code(
                pairing_code=sess["pairing_code"],
                cert_sha256=pin1,
                pairing_session_id=sess["pairing_session_id"],
                device_id=paired_id,
                client_nonce=nonce,
                server_nonce=req["server_nonce"],
            )
            assert sas == req["sas_code"]

            app_res = _run_win_cli(
                win_python,
                [
                    "-m",
                    "desktop.lan_pairing",
                    "approve",
                    "--state-dir",
                    state_dir_win,
                    "--request-id",
                    req["request_id"],
                    "--sas-code",
                    sas,
                ],
            )
            assert app_res["ok"] is True and app_res["status"] == "approved_pending_claim"

            st_done, done = pinned_https_request(
                host,
                port1,
                "POST",
                "/pair/complete",
                expected_cert_sha256=pin1,
                body={
                    "request_id": req["request_id"],
                    "device_id": paired_id,
                    "client_nonce": nonce,
                    "sas_code": sas,
                },
                timeout=10.0,
            )
            assert st_done == 200 and done["ok"] is True and done["cert_sha256"] == pin1
            auth_headers = {
                "Authorization": f"Bearer {done['device_token']}",
                "X-Device-Id": paired_id,
            }

            # 4. Bind paired device to authorized business identity & primary branch.
            bind_res = _run_win_cli(
                win_python,
                [
                    "-m",
                    "desktop.lan_pairing",
                    "bind-exchange",
                    "--state-dir",
                    state_dir_win,
                    "--device-id",
                    paired_id,
                    "--dataset-id",
                    ctx["dataset_id"],
                    "--source-device-id",
                    biz_id,
                    "--travel-id",
                    ctx["travel_id"],
                    "--branch-id",
                    ctx["branch_id"],
                ],
            )
            assert bind_res["ok"] is True

            # 5. Client delivers 0.1.0 expense.create (17.23 CNY) over pinned HTTPS.
            create_bytes = _make_create_bytes(
                dataset_id=ctx["dataset_id"],
                source_device_id=biz_id,
                operation_id="op-win-create-1",
                travel_id=ctx["travel_id"],
                branch_id=ctx["branch_id"],
                entity_id=entity_id,
                expected_revision_id=ctx["current_revision_id"],
                amount_cents=1723,
            )
            st_c, res_c = pinned_https_request(
                host,
                port1,
                "POST",
                "/exchange/expense-create",
                expected_cert_sha256=pin1,
                raw_body=create_bytes,
                headers=auth_headers,
                timeout=15.0,
            )
            assert st_c == 200 and res_c["ok"] is True and res_c["state"] == "committed"
            v_created = str(res_c["receipt"]["result_revision_id"])
            assert v_created and v_created != ctx["current_revision_id"]

            # 6. Desktop edits amount (17.23 -> 21.50) via existing FastAPI PUT handoff CLI.
            edit_res = _run_win_cli(
                win_python,
                [
                    handoff_script,
                    "edit-expense",
                    "--database",
                    trip_db_win,
                    "--version-id",
                    v_created,
                    "--expense-id",
                    entity_id,
                    "--amount",
                    "21.50",
                ],
            )
            assert edit_res["ok"] is True and edit_res["amount_cents"] == 2150
            v_patched = str(edit_res["version_id"])
            assert v_patched != v_created

            # 7. Client pulls pending expense.patch raw command bytes over pinned HTTPS.
            st_p, pull_res = pinned_https_request(
                host,
                port1,
                "POST",
                "/exchange/expense-patch/pull",
                expected_cert_sha256=pin1,
                body={},
                headers=auth_headers,
                timeout=15.0,
            )
            assert st_p == 200 and pull_res["ok"] is True and len(pull_res["items"]) == 1
            item = pull_res["items"][0]
            assert item["delivery_state"] == "verification_pending"
            assert item["expected_revision_id"] == v_created
            assert item["source_revision_id"] == v_patched

            # 8. Apply pulled raw bytes on isolated mobile replica & confirm receipt over HTTPS.
            mobile_db = tmp_path / "mobile_win_replica.sqlite"
            init_mobile_replica(
                mobile_db,
                defaults={
                    "dataset_id": ctx["dataset_id"],
                    "travel_id": ctx["travel_id"],
                    "branch_id": ctx["branch_id"],
                    "entity_id": entity_id,
                    "initial_local_revision_id": "m-rev-1",
                    "initial_synced_desktop_revision_id": v_created,
                    "initial_expense": {
                        "id": entity_id,
                        "title": "机场快轨",
                        "amount_cents": 1723,
                        "currency": "CNY",
                        "paid_on": None,
                        "category": "transport",
                        "notes": "Windows 原生业务服务实测",
                        "updated_at": "2026-11-26T12:00:00Z",
                    },
                },
            )
            mobile_receipt = apply_desktop_patch(
                mobile_db,
                item["raw_command_json"].encode("utf-8"),
                scope=PatchScope(
                    dataset_id=ctx["dataset_id"],
                    authorized_devices=frozenset({"desktop-device-1"}),
                    authorized_targets=frozenset({(ctx["travel_id"], ctx["branch_id"])}),
                ),
            )
            assert mobile_receipt["ok"] is True

            st_cf, cf_res = pinned_https_request(
                host,
                port1,
                "POST",
                "/exchange/expense-patch/confirm",
                expected_cert_sha256=pin1,
                body=mobile_receipt,
                headers=auth_headers,
                timeout=15.0,
            )
            assert st_cf == 200 and cf_res["ok"] is True and cf_res["confirmed"] is True
            assert cf_res["record"]["delivery_state"] == "confirmed"
            assert cf_res["record"]["confirmed_mobile_revision_id"] == "m-rev-2"

            # Duplicate confirm is idempotent.
            st_dup, dup_res = pinned_https_request(
                host,
                port1,
                "POST",
                "/exchange/expense-patch/confirm",
                expected_cert_sha256=pin1,
                body=mobile_receipt,
                headers=auth_headers,
                timeout=15.0,
            )
            assert st_dup == 200 and dup_res["already_confirmed"] is True
        finally:
            _stop_win_server(win_python, srv1, state_dir_arg=state_dir_win)

        # 9. Restart Windows Native foreground server on SAME state_dir & trip_db.
        srv2, info2 = _start_win_server(
            win_python,
            state_dir_arg=state_dir_win,
            bind_host=bind_host,
            trip_db_arg=trip_db_win,
            open_pairing=False,
        )
        try:
            port2 = int(info2["lan_port"])
            pin2 = str(info2["cert_sha256"])
            # Certificate identity MUST remain identical across restart.
            assert pin2 == pin1

            # Verify control status exposes lan_host, lan_port, cert_sha256, active device & grant.
            status = _run_win_cli(
                win_python,
                ["-m", "desktop.lan_pairing", "status", "--state-dir", state_dir_win],
            )
            assert status["ok"] is True
            assert status["lan_host"] == bind_host
            assert status["lan_port"] == port2
            assert status["cert_sha256"] == pin1
            assert status["exchange_enabled"] is True
            assert [d["device_id"] for d in status["devices"] if d["status"] == "active"] == [
                paired_id
            ]
            assert len(status["exchange_grants"]) == 1

            # Reconnected paired client reads fictional trip & sees 0 pending patches.
            st_trip, trip_res = pinned_https_request(
                bind_host,
                port2,
                "GET",
                "/trip/fictional",
                expected_cert_sha256=pin1,
                headers=auth_headers,
                timeout=10.0,
            )
            assert st_trip == 200 and trip_res["ok"] is True

            st_empty, empty_pull = pinned_https_request(
                bind_host,
                port2,
                "POST",
                "/exchange/expense-patch/pull",
                expected_cert_sha256=pin1,
                body={},
                headers=auth_headers,
                timeout=10.0,
            )
            assert st_empty == 200 and empty_pull == {"ok": True, "items": []}
        finally:
            _stop_win_server(win_python, srv2, state_dir_arg=state_dir_win)

        # 10. Verify persisted ledger amount, versions, UTF-8 titles, fork & outbound queue.
        insp = _run_win_cli(
            win_python,
            [
                handoff_script,
                "inspect-trip",
                "--database",
                trip_db_win,
                "--branch-id",
                ctx["branch_id"],
                "--fork-branch-id",
                ctx["fork_branch_id"],
            ],
        )
        assert insp["head_revision_id"] == v_patched
        assert insp["version_ids"] == [
            "409854fb9585",
            "42ac255b7c33",
            "9639db1eb3b8",
            v_created,
            v_patched,
        ]
        assert insp["head_paid_cents"] == 8635 + 2150
        assert [(e["id"], e["title"], e["amount_cents"]) for e in insp["head_expenses"]] == [
            ("d1-fictional-lunch", "虚构午餐", 8635),
            (entity_id, "机场快轨", 2150),
        ]
        assert insp["fork"]["head_expense_count"] == 0
        assert insp["pending_outbound_count"] == 0
        assert len(insp["outbound_operations"]) == 1
        assert insp["outbound_operations"][0]["delivery_state"] == "confirmed"
        assert insp["outbound_operations"][0]["confirmed_mobile_revision_id"] == "m-rev-2"

        out_stat = _run_win_cli(
            win_python,
            [handoff_script, "outbound-status", "--database", trip_db_win],
        )
        assert out_stat["ok"] is True
        assert out_stat["pending_count"] == 0
        assert out_stat["confirmed_count"] == 1
        assert out_stat["confirmed"][0]["confirmed_mobile_revision_id"] == "m-rev-2"
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
