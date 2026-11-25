"""Tests for the foreground LAN pairing prototype (TLS pin, mutual SAS, deferred claim, revoke)."""

from __future__ import annotations

import http.client
import json
import secrets
import ssl
import tempfile
import time
import unittest
from pathlib import Path

from desktop.lan_pairing import (
    ForegroundPairingServer,
    _control_call,
    compute_sas_code,
    pinned_https_request,
)


def _pair_device_via_control(
    state_dir: Path,
    host: str,
    port: int,
    pin: str,
    *,
    device_id: str,
    device_name: str,
    ttl_seconds: int = 60,
    complete: bool = True,
) -> dict:
    st_open, session = _control_call(
        state_dir, "POST", "/control/pairing/open", {"ttl_seconds": ttl_seconds}
    )
    assert st_open == 200
    client_nonce = secrets.token_hex(16)
    st_req, req = pinned_https_request(
        host,
        port,
        "POST",
        "/pair/request",
        expected_cert_sha256=pin,
        body={
            "pairing_session_id": session["pairing_session_id"],
            "pairing_code": session["pairing_code"],
            "device_id": device_id,
            "device_name": device_name,
            "client_nonce": client_nonce,
        },
    )
    assert st_req == 200
    sas = compute_sas_code(
        pairing_code=session["pairing_code"],
        cert_sha256=pin,
        pairing_session_id=session["pairing_session_id"],
        device_id=device_id,
        client_nonce=client_nonce,
        server_nonce=req["server_nonce"],
    )
    assert req["sas_code"] == sas
    st_app, app_res = _control_call(
        state_dir,
        "POST",
        "/control/pairing/approve",
        {"request_id": req["request_id"], "sas_code": sas},
    )
    assert st_app == 200
    assert app_res["status"] == "approved_pending_claim"

    out = {
        "request_id": req["request_id"],
        "client_nonce": client_nonce,
        "sas_code": sas,
        "device_token": None,
    }
    if complete:
        st_done, done = pinned_https_request(
            host,
            port,
            "POST",
            "/pair/complete",
            expected_cert_sha256=pin,
            body={
                "request_id": req["request_id"],
                "device_id": device_id,
                "client_nonce": client_nonce,
                "sas_code": sas,
            },
        )
        assert st_done == 200
        out["device_token"] = done["device_token"]
    return out


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

                # 2. Health works over pinned TLS; unpaired trip read is rejected (401).
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

                # LAN listener never exposes /control/*; loopback control rejects bad token (401).
                st_ctrl, _ = pinned_https_request(
                    host, port, "POST", "/control/stop", expected_cert_sha256=pin, body={}
                )
                self.assertEqual(st_ctrl, 404)

                ctrl_info = json.loads((state_dir / "control.json").read_text(encoding="utf-8"))
                bad_conn = http.client.HTTPConnection(
                    ctrl_info["control_host"], ctrl_info["control_port"], timeout=5.0
                )
                try:
                    bad_conn.request(
                        "GET", "/control/status", headers={"X-Control-Token": "wrong-token"}
                    )
                    self.assertEqual(bad_conn.getresponse().status, 401)
                finally:
                    bad_conn.close()

                # 3. Open pairing via real control endpoint; wrong pairing code rejected (403).
                st_open, session = _control_call(
                    state_dir, "POST", "/control/pairing/open", {"ttl_seconds": 60}
                )
                self.assertEqual(st_open, 200)
                client_nonce = secrets.token_hex(16)
                wrong_code = "000000" if session["pairing_code"] != "000000" else "999999"
                st_bad, bad = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/request",
                    expected_cert_sha256=pin,
                    body={
                        "pairing_session_id": session["pairing_session_id"],
                        "pairing_code": wrong_code,
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

                # 5. Desktop approves via real control endpoint -> approved_pending_claim (not yet
                #    activated in paired_devices until mobile claims!).
                wrong_sas = "000000" if expected_sas != "000000" else "999999"
                st_bad_sas, _ = _control_call(
                    state_dir,
                    "POST",
                    "/control/pairing/approve",
                    {"request_id": req["request_id"], "sas_code": wrong_sas},
                )
                self.assertEqual(st_bad_sas, 403)

                st_ok_app, app_res = _control_call(
                    state_dir,
                    "POST",
                    "/control/pairing/approve",
                    {"request_id": req["request_id"], "sas_code": expected_sas},
                )
                self.assertEqual(st_ok_app, 200)
                self.assertEqual(app_res["status"], "approved_pending_claim")

                st_stat, stat_before_claim = _control_call(state_dir, "GET", "/control/status")
                self.assertEqual(st_stat, 200)
                self.assertEqual(stat_before_claim["devices"], [])

                # Mobile completes pairing -> activates token once.
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

            # 7. Restart server: paired device reconnects without re-pairing.
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

                # 8. Revoke via real control endpoint -> immediately 403 device_revoked.
                st_rev_ctrl, rev_ctrl = _control_call(
                    state_dir, "POST", "/control/devices/revoke", {"device_id": "android-pixel-1"}
                )
                self.assertEqual(st_rev_ctrl, 200)
                self.assertTrue(rev_ctrl["revoked"])

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

    def test_interrupted_repairing_preserves_old_credential_across_expiry_and_restart(self) -> None:
        """Aborted same-device re-pairing must keep the old credential active and expire/drop."""
        with tempfile.TemporaryDirectory(prefix="lan-pairing-repair-") as tmp:
            state_dir = Path(tmp) / "state"
            srv = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0)
            info = srv.start()
            try:
                host, port, pin = info["lan_host"], info["lan_port"], info["cert_sha256"]
                paired = _pair_device_via_control(
                    state_dir,
                    host,
                    port,
                    pin,
                    device_id="android-pixel-1",
                    device_name="Pixel 9",
                    complete=True,
                )
                old_headers = {
                    "Authorization": f"Bearer {paired['device_token']}",
                    "X-Device-Id": "android-pixel-1",
                }

                # Case A: Re-pair approved into approved_pending_claim, then expires before claim.
                pending_exp = _pair_device_via_control(
                    state_dir,
                    host,
                    port,
                    pin,
                    device_id="android-pixel-1",
                    device_name="Pixel 9",
                    ttl_seconds=1,
                    complete=False,
                )
                # Old token still works while new request is pending claim.
                st_mid, _ = pinned_https_request(
                    host,
                    port,
                    "GET",
                    "/trip/fictional",
                    expected_cert_sha256=pin,
                    headers=old_headers,
                )
                self.assertEqual(st_mid, 200)

                time.sleep(1.15)
                st_exp, exp_res = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/complete",
                    expected_cert_sha256=pin,
                    body={
                        "request_id": pending_exp["request_id"],
                        "device_id": "android-pixel-1",
                        "client_nonce": pending_exp["client_nonce"],
                        "sas_code": pending_exp["sas_code"],
                    },
                )
                self.assertEqual(st_exp, 403)
                self.assertEqual(exp_res["error"], "pairing_request_expired")

                # Old token remains active after expired re-pairing!
                st_still, _ = pinned_https_request(
                    host,
                    port,
                    "GET",
                    "/trip/fictional",
                    expected_cert_sha256=pin,
                    headers=old_headers,
                )
                self.assertEqual(st_still, 200)

                # Case B: Re-pair approved, then desktop restarts before mobile claims.
                pending_restart = _pair_device_via_control(
                    state_dir,
                    host,
                    port,
                    pin,
                    device_id="android-pixel-1",
                    device_name="Pixel 9",
                    ttl_seconds=60,
                    complete=False,
                )
            finally:
                srv.stop()

            srv2 = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0)
            info2 = srv2.start()
            try:
                st_gone, gone_res = pinned_https_request(
                    info2["lan_host"],
                    info2["lan_port"],
                    "POST",
                    "/pair/complete",
                    expected_cert_sha256=pin,
                    body={
                        "request_id": pending_restart["request_id"],
                        "device_id": "android-pixel-1",
                        "client_nonce": pending_restart["client_nonce"],
                        "sas_code": pending_restart["sas_code"],
                    },
                )
                self.assertEqual(st_gone, 404)
                self.assertEqual(gone_res["error"], "request_not_found")

                # Old token still works across restart!
                st_ok2, _ = pinned_https_request(
                    info2["lan_host"],
                    info2["lan_port"],
                    "GET",
                    "/trip/fictional",
                    expected_cert_sha256=pin,
                    headers=old_headers,
                )
                self.assertEqual(st_ok2, 200)

                replacement = _pair_device_via_control(
                    state_dir,
                    info2["lan_host"],
                    info2["lan_port"],
                    pin,
                    device_id="android-pixel-1",
                    device_name="Pixel 9",
                )
                for token, expected_status in (
                    (old_headers["Authorization"], 401),
                    ("Bearer " + replacement["device_token"], 200),
                ):
                    status, _ = pinned_https_request(
                        info2["lan_host"],
                        info2["lan_port"],
                        "GET",
                        "/trip/fictional",
                        expected_cert_sha256=pin,
                        headers={**old_headers, "Authorization": token},
                    )
                    self.assertEqual(status, expected_status)
            finally:
                srv2.stop()

    def test_revoke_invalidates_pending_claim_and_blocks_completion(self) -> None:
        """Revoking a device while approved_pending_claim invalidates completion."""
        with tempfile.TemporaryDirectory(prefix="lan-pairing-revpending-") as tmp:
            state_dir = Path(tmp) / "state"
            srv = ForegroundPairingServer(state_dir, bind_host="127.0.0.1", port=0)
            info = srv.start()
            try:
                host, port, pin = info["lan_host"], info["lan_port"], info["cert_sha256"]
                pending = _pair_device_via_control(
                    state_dir,
                    host,
                    port,
                    pin,
                    device_id="android-rev-2",
                    device_name="Pixel Revoke",
                    complete=False,
                )
                st_rev, rev_res = _control_call(
                    state_dir, "POST", "/control/devices/revoke", {"device_id": "android-rev-2"}
                )
                self.assertEqual(st_rev, 200)
                self.assertTrue(rev_res["revoked"])

                st_comp, comp_res = pinned_https_request(
                    host,
                    port,
                    "POST",
                    "/pair/complete",
                    expected_cert_sha256=pin,
                    body={
                        "request_id": pending["request_id"],
                        "device_id": "android-rev-2",
                        "client_nonce": pending["client_nonce"],
                        "sas_code": pending["sas_code"],
                    },
                )
                self.assertEqual(st_comp, 403)
                self.assertEqual(comp_res["error"], "pairing_revoked")
            finally:
                srv.stop()


if __name__ == "__main__":
    unittest.main()
