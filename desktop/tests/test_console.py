"""Management permissions, confirmation and actual HTTP identity monitoring."""

import json
import secrets
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from desktop.console import Console, close_console, console_request, open_console
from desktop.lan_pairing import compute_sas_code, pinned_https_request
from desktop.lan_sync import sync_backend_python
from desktop.runtime import AlreadyRunning, check_services, prepare


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="travelflow console 中文 ")
        self.folder = prepare(Path(self.temporary.name))

    def tearDown(self):
        close_console(self.folder)
        self.temporary.cleanup()

    def test_reopen_reuses_console_and_http_rejects_unauthorized_clients(self):
        record = open_console(self.folder, browser=False)
        self.assertEqual(record["instance"], open_console(self.folder, browser=False)["instance"])
        base = f"http://127.0.0.1:{record['port']}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for headers in (
            {},
            {"X-TravelFlow-Control": record["token"], "Origin": "https://external.invalid"},
            {"X-TravelFlow-Control": record["token"], "Host": "external.invalid"},
        ):
            req = urllib.request.Request(base + "/api/state", headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                opener.open(req)
            self.assertEqual(raised.exception.code, 403)
            raised.exception.close()
        state = console_request(record, "/api/state")
        self.assertIsNone(state["runtime"])
        self.assertNotIn(record["token"], json.dumps(state))
        with opener.open(base) as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertNotIn(record["token"], response.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as raised:
            console_request(record, "/api/close", {})
        self.assertEqual(raised.exception.code, 409)
        raised.exception.close()

    def test_browser_failure_preserves_manager_and_retry_reuses_identity(self):
        record = open_console(self.folder, browser=False)
        for failure in (
            False,
            webbrowser.Error("private-launch-value"),
            OSError("private-launch-value"),
        ):
            options = (
                {"side_effect": failure}
                if isinstance(failure, Exception)
                else {"return_value": failure}
            )
            with (
                self.subTest(failure=type(failure).__name__),
                patch("desktop.console.webbrowser.open", **options),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    open_console(self.folder)
                self.assertNotIn("private-launch-value", str(raised.exception))
                self.assertNotIn(record["token"], str(raised.exception))
                self.assertEqual(console_request(record)["instance"], record["instance"])
                self.assertIsNone(console_request(record, "/api/state")["runtime"])
        with patch("desktop.console.webbrowser.open", return_value=True) as opening:
            self.assertEqual(open_console(self.folder)["instance"], record["instance"])
            opening.assert_called_once_with(f"http://127.0.0.1:{record['port']}/#{record['token']}")

    def test_preview_and_failed_confirmation_never_open_database(self):
        state = Console(self.folder)
        state.inspect({"profile": "application-qa", "ports": [18021, 18022]})
        selected = Path(state.preview["selection"]["database"])
        self.assertFalse(selected.exists())
        old_confirmation = state.preview["confirmation"]
        state.inspect({"profile": "application-qa", "ports": [18021, 18022]})
        with self.assertRaises(ValueError):
            state.launch({"confirmation": old_confirmation})
        with self.assertRaises(ValueError):
            state.launch({"confirmation": "wrong"})
        self.assertFalse(selected.exists())
        with self.assertRaises(ValueError):
            state.inspect({"profile": "application-qa", "ports": [18021, 18021]})
        with self.assertRaises(ValueError):
            state.inspect(
                {"profile": "application-qa", "ports": [18021, 18022], "env_file": "private.env"}
            )

    def test_preparation_job_rejects_duplicate_operations_and_reports_failure(self):
        state = Console(self.folder)
        entered, release = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            release.wait(5)
            raise RuntimeError("fixture preparation failed")

        with patch("desktop.console.prepare_application", blocked):
            state.submit("prepare", {})
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(AlreadyRunning):
                    state.submit("prepare", {})
            finally:
                release.set()
            deadline = time.monotonic() + 3
            while state.snapshot()["busy"] and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(state.snapshot()["error"], "fixture preparation failed")
        self.assertIsNone(state.snapshot()["busy"])

    def test_unverified_runtime_is_not_reported_as_stopped_or_removed(self):
        state = Console(self.folder)
        state.active = prepare(self.folder / "stale")
        path = state.active / "runtime.json"
        path.write_text("broken fixture", encoding="utf-8")
        self.assertTrue(state.snapshot()["runtime_unverified"])
        with self.assertRaises(RuntimeError):
            state.stop_application()
        self.assertEqual(path.read_text(), "broken fixture")

    def test_live_health_rejects_changed_identity_and_keeps_original_paths(self):
        original = {"instance": "this-run", "role": "backend", "database": "expected.sqlite"}
        reply = dict(original)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(reply).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:

            def checked():
                return check_services(
                    [server.server_port],
                    [(None, None, None, "/health", None)],
                    [original],
                    "this-run",
                    "fixture",
                )

            self.assertTrue(checked()[0]["healthy"])
            reply["database"] = "unexpected.sqlite"
            result = checked()[0]
            self.assertFalse(result["healthy"])
            self.assertEqual(result["database"], "expected.sqlite")
            reply.clear()
            reply.update(original)
            self.assertTrue(checked()[0]["healthy"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def _wait_idle(self, record, *, timeout=35.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snap = console_request(record, "/api/state")
            if not snap["busy"]:
                return snap
            time.sleep(0.1)
        self.fail("Console action did not finish in time")

    @unittest.skipUnless(sync_backend_python() is not None, "Requires backend Python 3.13+ venv")
    def test_console_lan_sync_lifecycle_unauthorized_authorized_revoked_and_restart(self):
        record = open_console(self.folder, browser=False)
        console_request(
            record,
            "/api/sync-start",
            {"bind_host": "127.0.0.1", "port": 0, "san_ips": ""},
        )
        snap = self._wait_idle(record)
        self.assertIsNone(snap["error"])
        sync = snap["sync"]
        self.assertTrue(sync["running"])
        self.assertTrue(sync["exchange_enabled"])
        pairing = sync["pairing"]
        self.assertIsNotNone(pairing)
        self.assertNotIn("control_token", sync["pairing_json"])
        self.assertNotIn("control_port", sync["pairing_json"])
        self.assertNotIn(record["token"], json.dumps(snap))

        # Verify pairing-state/control.json token never leaks into /api/state.
        ctrl_json = json.loads(
            (self.folder / "lan-sync" / "pairing-state" / "control.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn(ctrl_json["control_token"], json.dumps(snap))

        host = pairing["lan_host"]
        port = pairing["lan_port"]
        pin = pairing["cert_sha256"]
        trip = pairing["trip"]
        paired_id = "ref-android-console-1"
        biz_id = "ref-android-biz-1"
        nonce = secrets.token_hex(16)

        # 1. Reference client initiates pairing using ONLY the bundle from console state.
        st_req, req = pinned_https_request(
            host,
            port,
            "POST",
            "/pair/request",
            expected_cert_sha256=pin,
            body={
                "pairing_session_id": pairing["pairing_session_id"],
                "pairing_code": pairing["pairing_code"],
                "device_id": paired_id,
                "device_name": "Console Reference Phone",
                "client_nonce": nonce,
            },
        )
        self.assertEqual(st_req, 200)
        sas = compute_sas_code(
            pairing_code=pairing["pairing_code"],
            cert_sha256=pin,
            pairing_session_id=pairing["pairing_session_id"],
            device_id=paired_id,
            client_nonce=nonce,
            server_nonce=req["server_nonce"],
        )
        self.assertEqual(sas, req["sas_code"])

        # Console state exposes the pending request with device name & 6-digit SAS code.
        snap = console_request(record, "/api/state")
        pending = snap["sync"]["pending_requests"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["device_id"], paired_id)
        self.assertEqual(pending[0]["sas_code"], sas)

        # Wrong SAS code is rejected cleanly.
        wrong_sas = "000000" if sas != "000000" else "999999"
        console_request(
            record,
            "/api/sync-approve",
            {"request_id": req["request_id"], "sas_code": wrong_sas},
        )
        snap_err = self._wait_idle(record)
        self.assertIn("短码不匹配", snap_err["error"] or "")

        # Right SAS code approves pairing; client claims token.
        console_request(
            record,
            "/api/sync-approve",
            {"request_id": req["request_id"], "sas_code": sas},
        )
        snap = self._wait_idle(record)
        self.assertIsNone(snap["error"])

        st_done, done = pinned_https_request(
            host,
            port,
            "POST",
            "/pair/complete",
            expected_cert_sha256=pin,
            body={
                "request_id": req["request_id"],
                "device_id": paired_id,
                "client_nonce": nonce,
                "sas_code": sas,
            },
        )
        self.assertEqual(st_done, 200)
        auth_headers = {
            "Authorization": f"Bearer {done['device_token']}",
            "X-Device-Id": paired_id,
        }

        # 2. BEFORE separate trip authorization -> 403 exchange_not_authorized, zero writes.
        create_raw = json.dumps(
            {
                "profile": "p1-host-control-1",
                "kind": "expense.create",
                "operation": {
                    "dataset_id": trip["dataset_id"],
                    "source_device_id": biz_id,
                    "operation_id": "op-console-ui-1",
                },
                "target": {
                    "travel_id": trip["travel_id"],
                    "branch_id": trip["branch_id"],
                    "entity_id": "console-metro-1",
                },
                "expected_revision_id": trip["current_revision_id"],
                "payload": {
                    "title": "机场快轨",
                    "amount_cents": 1723,
                    "currency": "CNY",
                    "paid_on": None,
                    "category": "transport",
                    "day": None,
                    "activity_id": None,
                    "notes": "管理界面实测",
                    "receipt_ids": [],
                    "allow_shared_receipts": False,
                    "budget_item_id": None,
                    "settles_budget": False,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        lookup_raw = json.dumps(
            {
                "profile": "p1-host-query-1",
                "kind": "receipt.lookup",
                "operation": {
                    "dataset_id": trip["dataset_id"],
                    "source_device_id": biz_id,
                    "operation_id": "op-console-ui-1",
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

        for route, raw in (
            ("/exchange/expense-create", create_raw),
            ("/exchange/receipt-lookup", lookup_raw),
            ("/exchange/expense-patch/pull", b"{}"),
        ):
            st_pre, res_pre = pinned_https_request(
                host,
                port,
                "POST",
                route,
                expected_cert_sha256=pin,
                raw_body=raw,
                headers=auth_headers,
            )
            self.assertEqual(st_pre, 403)
            self.assertEqual(res_pre["error"], "exchange_not_authorized")

        # 3. Separately authorize the isolated test trip via console control plane.
        console_request(
            record,
            "/api/sync-authorize",
            {"device_id": paired_id, "source_device_id": biz_id},
        )
        snap = self._wait_idle(record)
        self.assertIsNone(snap["error"])
        self.assertEqual(len(snap["sync"]["exchange_grants"]), 1)
        self.assertEqual(snap["sync"]["exchange_grants"][0]["source_device_id"], biz_id)

        # AFTER authorization -> expense.create & receipt.lookup succeed!
        st_c, res_c = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/expense-create",
            expected_cert_sha256=pin,
            raw_body=create_raw,
            headers=auth_headers,
        )
        self.assertEqual(st_c, 200)
        self.assertTrue(res_c["ok"])
        v_created = res_c["receipt"]["result_revision_id"]

        st_l, res_l = pinned_https_request(
            host,
            port,
            "POST",
            "/exchange/receipt-lookup",
            expected_cert_sha256=pin,
            raw_body=lookup_raw,
            headers=auth_headers,
        )
        self.assertEqual(st_l, 200)
        self.assertEqual(res_l["receipt"]["result_revision_id"], v_created)

        snap = console_request(record, "/api/state")
        self.assertEqual(snap["sync"]["trip"]["expense_count"], 2)
        self.assertEqual(snap["sync"]["trip"]["paid_cents"], 8635 + 1723)

        # 4. Restart sync service: cert identity, active grant & ledger survive.
        console_request(record, "/api/sync-stop", {})
        snap = self._wait_idle(record)
        self.assertFalse(snap["sync"]["running"])
        self.assertEqual(snap["sync"]["cert_sha256"], pin)
        self.assertEqual(len(snap["sync"]["exchange_grants"]), 1)

        console_request(
            record,
            "/api/sync-start",
            {"bind_host": "127.0.0.1", "port": 0, "san_ips": ""},
        )
        snap = self._wait_idle(record)
        self.assertTrue(snap["sync"]["running"])
        self.assertEqual(snap["sync"]["cert_sha256"], pin)
        port2 = snap["sync"]["lan_port"]
        st_l2, res_l2 = pinned_https_request(
            host,
            port2,
            "POST",
            "/exchange/receipt-lookup",
            expected_cert_sha256=pin,
            raw_body=lookup_raw,
            headers=auth_headers,
        )
        self.assertEqual(st_l2, 200)
        self.assertEqual(res_l2["receipt"]["result_revision_id"], v_created)

        # 5. Revoke device via console -> immediately rejected with 403 device_revoked.
        console_request(record, "/api/sync-revoke", {"device_id": paired_id})
        snap = self._wait_idle(record)
        self.assertIsNone(snap["error"])
        self.assertEqual(snap["sync"]["exchange_grants"], [])
        self.assertEqual(snap["sync"]["devices"][0]["status"], "revoked")

        st_rev, res_rev = pinned_https_request(
            host,
            port2,
            "POST",
            "/exchange/receipt-lookup",
            expected_cert_sha256=pin,
            raw_body=lookup_raw,
            headers=auth_headers,
        )
        self.assertEqual(st_rev, 403)
        self.assertEqual(res_rev["error"], "device_revoked")

        # 6. Full console exit & reopen: persisted cert, revoked status & ledger intact.
        close_console(self.folder)
        record2 = open_console(self.folder, browser=False)
        snap2 = console_request(record2, "/api/state")
        self.assertFalse(snap2["sync"]["running"])
        self.assertEqual(snap2["sync"]["cert_sha256"], pin)
        self.assertEqual(snap2["sync"]["devices"][0]["status"], "revoked")
        self.assertEqual(snap2["sync"]["exchange_grants"], [])
        self.assertEqual(snap2["sync"]["trip"]["paid_cents"], 8635 + 1723)

        # Restart sync service after console reopen -> revoked device remains blocked.
        console_request(
            record2,
            "/api/sync-start",
            {"bind_host": "127.0.0.1", "port": 0, "san_ips": ""},
        )
        snap2 = self._wait_idle(record2)
        self.assertTrue(snap2["sync"]["running"])
        self.assertEqual(snap2["sync"]["cert_sha256"], pin)
        st_rev2, res_rev2 = pinned_https_request(
            host,
            snap2["sync"]["lan_port"],
            "POST",
            "/exchange/receipt-lookup",
            expected_cert_sha256=pin,
            raw_body=lookup_raw,
            headers=auth_headers,
        )
        self.assertEqual(st_rev2, 403)
        self.assertEqual(res_rev2["error"], "device_revoked")


if __name__ == "__main__":
    unittest.main()
