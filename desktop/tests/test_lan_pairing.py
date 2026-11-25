"""Tests for the foreground LAN pairing prototype (TLS pin, mutual SAS, reconnect, revocation)."""

from __future__ import annotations

import secrets
import ssl
import tempfile
import unittest
from pathlib import Path

from desktop.lan_pairing import (
    ForegroundPairingServer,
    compute_sas_code,
    pinned_https_request,
)


class LanPairingPrototypeTests(unittest.TestCase):
    def test_full_pairing_reconnect_revocation_and_security_guards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lan-pairing-test-") as tmp:
            state_dir = Path(tmp) / "state"
            srv = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0)
            info = srv.start()
            try:
                host = info["lan_host"]
                port = info["lan_port"]
                pin = info["cert_sha256"]

                # 1. Wrong cert pin must fail TLS handshake verification.
                with self.assertRaises(ssl.SSLError):
                    pinned_https_request(
                        host, port, "GET", "/health", expected_cert_sha256="0" * 64
                    )

                # 2. Health endpoint works over pinned TLS; unpaired trip read is rejected (401).
                st_h, health = pinned_https_request(
                    host, port, "GET", "/health", expected_cert_sha256=pin
                )
                self.assertEqual(st_h, 200)
                self.assertEqual(health["cert_sha256"], pin)

                st_unauth, unauth = pinned_https_request(
                    host, port, "GET", "/trip/fictional", expected_cert_sha256=pin
                )
                self.assertEqual(st_unauth, 401)
                self.assertEqual(unauth, {"ok": False, "error": "unauthorized"})
                self.assertNotIn("trip", unauth)

                # LAN listener must never expose administrative /control/* routes.
                st_ctrl, _ = pinned_https_request(
                    host, port, "POST", "/control/stop", expected_cert_sha256=pin, body={}
                )
                self.assertEqual(st_ctrl, 404)

                # 3. Open single-use pairing session; wrong pairing code rejected (403).
                session = srv.coordinator.open_pairing_session(ttl_seconds=60)
                client_nonce = secrets.token_hex(16)
                st_bad, bad = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/request",
                    expected_cert_sha256=pin,
                    body={
                        "pairing_session_id": session["pairing_session_id"],
                        "pairing_code": "000000"
                        if session["pairing_code"] != "000000"
                        else "999999",
                        "device_id": "android-pixel-1",
                        "device_name": "Pixel Test",
                        "client_nonce": client_nonce,
                    },
                )
                self.assertEqual(st_bad, 403)
                self.assertEqual(bad["error"], "invalid_pairing_code")

                # 4. Valid pair request enters awaiting_desktop_confirm; mobile cannot complete yet.
                st_req, req = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/request",
                    expected_cert_sha256=pin,
                    body={
                        "pairing_session_id": session["pairing_session_id"],
                        "pairing_code": session["pairing_code"],
                        "device_id": "android-pixel-1",
                        "device_name": "Pixel Test",
                        "client_nonce": client_nonce,
                    },
                )
                self.assertEqual(st_req, 200)
                self.assertEqual(req["status"], "awaiting_desktop_confirm")

                expected_sas = compute_sas_code(
                    pairing_code=session["pairing_code"],
                    cert_sha256=pin,
                    pairing_session_id=session["pairing_session_id"],
                    device_id="android-pixel-1",
                    client_nonce=client_nonce,
                    server_nonce=req["server_nonce"],
                )
                self.assertEqual(req["sas_code"], expected_sas)

                st_early, early = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/complete",
                    expected_cert_sha256=pin,
                    body={
                        "request_id": req["request_id"],
                        "device_id": "android-pixel-1",
                        "client_nonce": client_nonce,
                        "sas_code": expected_sas,
                    },
                )
                self.assertEqual(st_early, 409)
                self.assertEqual(early["error"], "awaiting_desktop_confirm")

                # 5. Desktop explicitly approves with matching SAS; wrong SAS rejected.
                st_bad_sas, _ = srv.coordinator.approve_request(
                    req["request_id"], "000000" if expected_sas != "000000" else "999999"
                )
                self.assertEqual(st_bad_sas, 403)

                st_ok_app, _ = srv.coordinator.approve_request(req["request_id"], expected_sas)
                self.assertEqual(st_ok_app, 200)

                # Mobile completes pairing and receives one-time device token.
                st_done, done = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/complete",
                    expected_cert_sha256=pin,
                    body={
                        "request_id": req["request_id"],
                        "device_id": "android-pixel-1",
                        "client_nonce": client_nonce,
                        "sas_code": expected_sas,
                    },
                )
                self.assertEqual(st_done, 200)
                self.assertEqual(done["status"], "paired")
                device_token = done["device_token"]
                self.assertTrue(len(device_token) >= 32)

                # One-time token delivery cannot be replayed.
                st_rep, rep = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/complete",
                    expected_cert_sha256=pin,
                    body={
                        "request_id": req["request_id"],
                        "device_id": "android-pixel-1",
                        "client_nonce": client_nonce,
                        "sas_code": expected_sas,
                    },
                )
                self.assertEqual(st_rep, 409)
                self.assertEqual(rep["error"], "token_already_delivered")

                # 6. Paired device reads fictional trip over pinned TLS.
                auth_headers = {
                    "Authorization": f"Bearer {device_token}",
                    "X-Device-Id": "android-pixel-1",
                }
                st_trip, trip_res = pinned_https_request(
                    host,
                    port,
                    "GET",
                    "/trip/fictional",
                    expected_cert_sha256=pin,
                    headers=auth_headers,
                )
                self.assertEqual(st_trip, 200)
                self.assertTrue(trip_res["trip"]["fictional_only"])
                self.assertEqual(trip_res["trip"]["sample_expense_amount_cents"], 8635)
            finally:
                srv.stop()

            # 7. Restart server from same state_dir: paired device reconnects without re-pairing!
            srv2 = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0)
            info2 = srv2.start()
            try:
                self.assertEqual(info2["cert_sha256"], pin)
                st_recon, recon = pinned_https_request(
                    info2["lan_host"],
                    info2["lan_port"],
                    "GET",
                    "/trip/fictional",
                    expected_cert_sha256=pin,
                    headers=auth_headers,
                )
                self.assertEqual(st_recon, 200)
                self.assertEqual(recon["trip"]["travel_id"], "92ba6a148a17")

                # 8. Revoke device -> immediately rejected (403 device_revoked), no data leaked.
                self.assertTrue(srv2.store.revoke_device("android-pixel-1"))
                st_rev, rev = pinned_https_request(
                    info2["lan_host"],
                    info2["lan_port"],
                    "GET",
                    "/trip/fictional",
                    expected_cert_sha256=pin,
                    headers=auth_headers,
                )
                self.assertEqual(st_rev, 403)
                self.assertEqual(rev, {"ok": False, "error": "device_revoked"})
                self.assertNotIn("trip", rev)
            finally:
                srv2.stop()

            # 9. Restart again: revocation persists across restarts.
            srv3 = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0)
            info3 = srv3.start()
            try:
                st_rev2, rev2 = pinned_https_request(
                    info3["lan_host"],
                    info3["lan_port"],
                    "GET",
                    "/trip/fictional",
                    expected_cert_sha256=pin,
                    headers=auth_headers,
                )
                self.assertEqual(st_rev2, 403)
                self.assertEqual(rev2, {"ok": False, "error": "device_revoked"})
            finally:
                srv3.stop()


if __name__ == "__main__":
    unittest.main()
