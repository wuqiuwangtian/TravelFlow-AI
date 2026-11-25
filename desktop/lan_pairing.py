"""Foreground LAN pairing prototype with TLS 1.2+/1.3 cert pinning, mutual SAS, and revocation.

Boundaries:
- Runs strictly in the foreground with explicit start and stop (no background daemon).
- Uses only fictional trip data (from desktop/tests/fixtures/d1/expected.json); never opens
  personal databases or .env.
- Separates the LAN HTTPS listener (/health, /pair/request, /pair/complete, /trip/fictional)
  from the loopback-only 127.0.0.1 control endpoint (/control/* authenticated by a 0600
  per-run X-Control-Token).
- Uses standard OpenSSL ECDSA P-256 X.509 certificates, TLS 1.2+/1.3, SHA-256 certificate
  pinning, HMAC-SHA256 6-digit mutual SAS confirmation, and SHA-256 hashed 256-bit device tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FICTIONAL_EXPECTED_JSON = REPO_ROOT / "desktop" / "tests" / "fixtures" / "d1" / "expected.json"
PROTOCOL_VERSION = "travelflow-lan-pairing/0-draft"
DEFAULT_PAIRING_TTL_SECONDS = 120

PAIRING_SCHEMA = """
CREATE TABLE IF NOT EXISTS paired_devices (
    device_id TEXT PRIMARY KEY,
    device_name TEXT NOT NULL,
    token_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    paired_at TEXT NOT NULL,
    revoked_at TEXT
);
"""


def _find_openssl() -> str:
    found = shutil.which("openssl")
    if found:
        return found
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\openssl.exe",
            r"C:\Program Files\Git\mingw64\bin\openssl.exe",
        ):
            if Path(candidate).is_file():
                return candidate
    raise RuntimeError("openssl executable not found in PATH")


def _chmod_0600(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)


def ensure_tls_identity(state_dir: Path, san_ips: list[str]) -> tuple[Path, Path, str]:
    """Ensure a persistent ECDSA P-256 self-signed cert/key pair and return its DER SHA-256."""
    state_dir.mkdir(parents=True, exist_ok=True)
    cert_path = state_dir / "server.crt"
    key_path = state_dir / "server.key"
    if not (cert_path.is_file() and key_path.is_file()):
        ips = sorted({"127.0.0.1", *(ip for ip in san_ips if ip and ip != "0.0.0.0")})
        san_entries = ",".join(f"IP:{ip}" for ip in ips)
        ext_cfg = state_dir / "openssl-san.cnf"
        ext_cfg.write_text(
            "[req]\n"
            "distinguished_name = dn\n"
            "x509_extensions = v3_req\n"
            "prompt = no\n"
            "[dn]\n"
            "CN = TravelFlow-LAN-Pairing-Prototype\n"
            "[v3_req]\n"
            "basicConstraints = CA:FALSE\n"
            "keyUsage = digitalSignature, keyEncipherment\n"
            "extendedKeyUsage = serverAuth\n"
            f"subjectAltName = {san_entries}\n",
            encoding="utf-8",
        )
        openssl = _find_openssl()
        proc = subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:prime256v1",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-days",
                "30",
                "-nodes",
                "-config",
                str(ext_cfg),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"openssl cert generation failed: {proc.stderr.strip()}")
        _chmod_0600(key_path)

    der_bytes = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="utf-8"))
    cert_sha256 = hashlib.sha256(der_bytes).hexdigest()
    return cert_path, key_path, cert_sha256


def compute_sas_code(
    *,
    pairing_code: str,
    cert_sha256: str,
    pairing_session_id: str,
    device_id: str,
    client_nonce: str,
    server_nonce: str,
) -> str:
    """Derive a 6-digit mutual Short Authentication String using HMAC-SHA256."""
    msg = "|".join((cert_sha256, pairing_session_id, device_id, client_nonce, server_nonce)).encode(
        "utf-8"
    )
    digest = hmac.new(pairing_code.encode("utf-8"), msg, hashlib.sha256).digest()
    num = int.from_bytes(digest[:4], "big") % 1_000_000
    return f"{num:06d}"


def _load_fictional_trip() -> dict[str, Any]:
    expected = json.loads(FICTIONAL_EXPECTED_JSON.read_bytes().decode("utf-8"))
    return {
        "fictional_only": True,
        "travel_id": expected["session"],
        "title": expected["title"],
        "main_branch_id": expected["main_branch"],
        "head_revision_id": expected["main_versions"][-1],
        "sample_expense_amount_cents": expected["amount_cents"],
    }


class PairingStore:
    """Isolated SQLite store for paired and revoked devices (stores token SHA-256 only)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path, isolation_level=None)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(PAIRING_SCHEMA)
        _chmod_0600(self.db_path)

    def save_paired_device(self, device_id: str, device_name: str, token_sha256: str) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.db_path, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO paired_devices(device_id, device_name, token_sha256, status, "
                "paired_at, revoked_at) VALUES (?, ?, ?, 'active', ?, NULL) "
                "ON CONFLICT(device_id) DO UPDATE SET "
                "device_name = excluded.device_name, token_sha256 = excluded.token_sha256, "
                "status = 'active', paired_at = excluded.paired_at, revoked_at = NULL",
                (device_id, device_name, token_sha256, now),
            )
            db.execute("COMMIT")

    def revoke_device(self, device_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.db_path, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(
                "UPDATE paired_devices SET status = 'revoked', revoked_at = ? "
                "WHERE device_id = ? AND status = 'active'",
                (now, device_id),
            )
            db.execute("COMMIT")
            return cur.rowcount > 0

    def verify_bearer(self, device_id: str, raw_token: str) -> str:
        """Return 'active', 'revoked', or 'unauthorized' using constant-time hash comparison."""
        token_sha = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT token_sha256, status FROM paired_devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return "unauthorized"
        if row["status"] == "revoked":
            return "revoked"
        if not hmac.compare_digest(str(row["token_sha256"]), token_sha):
            return "unauthorized"
        return "active"

    def list_devices(self) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT device_id, device_name, status, paired_at, revoked_at "
                "FROM paired_devices ORDER BY paired_at, device_id"
            ).fetchall()
        return [dict(r) for r in rows]


class PairingCoordinator:
    """In-memory state machine for time-limited pairing sessions + persistent SQLite store."""

    def __init__(self, store: PairingStore, cert_sha256: str) -> None:
        self.store = store
        self.cert_sha256 = cert_sha256
        self.fictional_trip = _load_fictional_trip()
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, dict[str, Any]] = {}

    def open_pairing_session(
        self, ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS
    ) -> dict[str, Any]:
        session_id = secrets.token_hex(16)
        pairing_code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._sessions[session_id] = {
                "pairing_session_id": session_id,
                "pairing_code": pairing_code,
                "expires_at": expires_at,
                "consumed": False,
            }
        return {
            "pairing_session_id": session_id,
            "pairing_code": pairing_code,
            "cert_sha256": self.cert_sha256,
            "expires_at": expires_at.isoformat(),
        }

    def handle_pair_request(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        sid = body.get("pairing_session_id")
        code = body.get("pairing_code")
        device_id = body.get("device_id")
        device_name = body.get("device_name")
        client_nonce = body.get("client_nonce")
        if not all(
            isinstance(x, str) and 1 <= len(x) <= 128
            for x in (sid, code, device_id, device_name, client_nonce)
        ):
            return 400, {"ok": False, "error": "invalid_request"}
        assert isinstance(sid, str) and isinstance(code, str)
        assert isinstance(device_id, str) and isinstance(device_name, str)
        assert isinstance(client_nonce, str)

        with self._lock:
            sess = self._sessions.get(sid)
            if sess is None or sess["consumed"]:
                return 403, {"ok": False, "error": "invalid_pairing_session"}
            if datetime.now(UTC) > sess["expires_at"]:
                return 403, {"ok": False, "error": "pairing_session_expired"}
            if not hmac.compare_digest(code, sess["pairing_code"]):
                return 403, {"ok": False, "error": "invalid_pairing_code"}

            server_nonce = secrets.token_hex(16)
            sas_code = compute_sas_code(
                pairing_code=sess["pairing_code"],
                cert_sha256=self.cert_sha256,
                pairing_session_id=sid,
                device_id=device_id,
                client_nonce=client_nonce,
                server_nonce=server_nonce,
            )
            request_id = secrets.token_hex(12)
            self._requests[request_id] = {
                "request_id": request_id,
                "pairing_session_id": sid,
                "device_id": device_id,
                "device_name": device_name,
                "client_nonce": client_nonce,
                "server_nonce": server_nonce,
                "sas_code": sas_code,
                "status": "awaiting_desktop_confirm",
                "one_time_token": None,
                "token_delivered": False,
            }
        return 200, {
            "ok": True,
            "request_id": request_id,
            "server_nonce": server_nonce,
            "sas_code": sas_code,
            "status": "awaiting_desktop_confirm",
        }

    def approve_request(self, request_id: str, sas_code: str) -> tuple[int, dict[str, Any]]:
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                return 404, {"ok": False, "error": "request_not_found"}
            if req["status"] != "awaiting_desktop_confirm":
                return 409, {"ok": False, "error": f"invalid_state:{req['status']}"}
            if not hmac.compare_digest(sas_code, req["sas_code"]):
                return 403, {"ok": False, "error": "sas_mismatch"}
            sess = self._sessions.get(req["pairing_session_id"])
            if sess is None or sess["consumed"] or datetime.now(UTC) > sess["expires_at"]:
                return 409, {"ok": False, "error": "pairing_session_unavailable"}

            raw_token = secrets.token_urlsafe(32)
            token_sha256 = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            self.store.save_paired_device(req["device_id"], req["device_name"], token_sha256)
            sess["consumed"] = True
            req["status"] = "approved"
            req["one_time_token"] = raw_token
            return 200, {
                "ok": True,
                "request_id": request_id,
                "device_id": req["device_id"],
                "status": "approved",
            }

    def reject_request(self, request_id: str) -> tuple[int, dict[str, Any]]:
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                return 404, {"ok": False, "error": "request_not_found"}
            req["status"] = "rejected"
            req["one_time_token"] = None
            return 200, {"ok": True, "request_id": request_id, "status": "rejected"}

    def handle_pair_complete(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request_id = body.get("request_id")
        device_id = body.get("device_id")
        client_nonce = body.get("client_nonce")
        sas_code = body.get("sas_code")
        if not all(
            isinstance(x, str) and 1 <= len(x) <= 128
            for x in (request_id, device_id, client_nonce, sas_code)
        ):
            return 400, {"ok": False, "error": "invalid_request"}
        assert isinstance(request_id, str) and isinstance(device_id, str)
        assert isinstance(client_nonce, str) and isinstance(sas_code, str)

        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                return 404, {"ok": False, "error": "request_not_found"}
            if (
                req["device_id"] != device_id
                or not hmac.compare_digest(req["client_nonce"], client_nonce)
                or not hmac.compare_digest(req["sas_code"], sas_code)
            ):
                return 403, {"ok": False, "error": "pairing_proof_mismatch"}
            if req["status"] == "awaiting_desktop_confirm":
                return 409, {"ok": False, "error": "awaiting_desktop_confirm"}
            if req["status"] == "rejected":
                return 403, {"ok": False, "error": "pairing_rejected"}
            if req["token_delivered"] or not req["one_time_token"]:
                return 409, {"ok": False, "error": "token_already_delivered"}

            token = req["one_time_token"]
            req["one_time_token"] = None
            req["token_delivered"] = True
            return 200, {
                "ok": True,
                "status": "paired",
                "device_id": device_id,
                "device_token": token,
                "cert_sha256": self.cert_sha256,
            }

    def list_pending_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "request_id": r["request_id"],
                    "device_id": r["device_id"],
                    "device_name": r["device_name"],
                    "sas_code": r["sas_code"],
                    "status": r["status"],
                }
                for r in self._requests.values()
            ]


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    length_hdr = handler.headers.get("Content-Length")
    if not length_hdr:
        return {}
    try:
        length = int(length_hdr)
    except ValueError:
        return None
    if length < 0 or length > 65536:
        return None
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _make_lan_handler(coordinator: PairingCoordinator) -> type[BaseHTTPRequestHandler]:
    class LanHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            if self.path == "/health":
                _send_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "status": "ok",
                        "protocol": PROTOCOL_VERSION,
                        "cert_sha256": coordinator.cert_sha256,
                    },
                )
                return
            if self.path == "/trip/fictional":
                auth = self.headers.get("Authorization", "")
                device_id = self.headers.get("X-Device-Id", "")
                if not auth.startswith("Bearer ") or not device_id:
                    _send_json(self, 401, {"ok": False, "error": "unauthorized"})
                    return
                token = auth[len("Bearer ") :]
                verdict = coordinator.store.verify_bearer(device_id, token)
                if verdict == "revoked":
                    _send_json(self, 403, {"ok": False, "error": "device_revoked"})
                    return
                if verdict != "active":
                    _send_json(self, 401, {"ok": False, "error": "unauthorized"})
                    return
                _send_json(self, 200, {"ok": True, "trip": coordinator.fictional_trip})
                return
            _send_json(self, 404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            body = _read_json_body(self)
            if body is None:
                _send_json(self, 400, {"ok": False, "error": "invalid_json"})
                return
            if self.path == "/pair/request":
                code, payload = coordinator.handle_pair_request(body)
                _send_json(self, code, payload)
                return
            if self.path == "/pair/complete":
                code, payload = coordinator.handle_pair_complete(body)
                _send_json(self, code, payload)
                return
            _send_json(self, 404, {"ok": False, "error": "not_found"})

    return LanHandler


def _make_control_handler(
    coordinator: PairingCoordinator,
    control_token: str,
    stop_event: threading.Event,
) -> type[BaseHTTPRequestHandler]:
    class ControlHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _check_auth(self) -> bool:
            got = self.headers.get("X-Control-Token", "")
            if not hmac.compare_digest(got, control_token):
                _send_json(self, 401, {"ok": False, "error": "unauthorized"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._check_auth():
                return
            if self.path == "/control/status":
                _send_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "cert_sha256": coordinator.cert_sha256,
                        "pending_requests": coordinator.list_pending_requests(),
                        "devices": coordinator.store.list_devices(),
                    },
                )
                return
            _send_json(self, 404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            if not self._check_auth():
                return
            body = _read_json_body(self)
            if body is None:
                _send_json(self, 400, {"ok": False, "error": "invalid_json"})
                return
            if self.path == "/control/pairing/open":
                ttl = int(body.get("ttl_seconds", DEFAULT_PAIRING_TTL_SECONDS))
                _send_json(self, 200, {"ok": True, **coordinator.open_pairing_session(ttl)})
                return
            if self.path == "/control/pairing/approve":
                code, payload = coordinator.approve_request(
                    str(body.get("request_id", "")), str(body.get("sas_code", ""))
                )
                _send_json(self, code, payload)
                return
            if self.path == "/control/pairing/reject":
                code, payload = coordinator.reject_request(str(body.get("request_id", "")))
                _send_json(self, code, payload)
                return
            if self.path == "/control/devices/revoke":
                device_id = str(body.get("device_id", ""))
                revoked = coordinator.store.revoke_device(device_id)
                _send_json(
                    self,
                    200 if revoked else 404,
                    {"ok": revoked, "device_id": device_id, "revoked": revoked},
                )
                return
            if self.path == "/control/stop":
                _send_json(self, 200, {"ok": True, "stopping": True})
                stop_event.set()
                return
            _send_json(self, 404, {"ok": False, "error": "not_found"})

    return ControlHandler


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Suppress expected client-abort SSL/pipe tracebacks (e.g. rejected certificate pin)."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ssl.SSLError)):
            return
        super().handle_error(request, client_address)


class ForegroundPairingServer:
    """Manage the foreground HTTPS LAN listener and loopback-only control listener."""

    def __init__(
        self,
        state_dir: Path,
        *,
        bind_host: str = "127.0.0.1",
        port: int = 0,
        san_ips: list[str] | None = None,
    ) -> None:
        self.state_dir = state_dir.resolve()
        self.bind_host = bind_host
        ips = [bind_host, *(san_ips or [])]
        self.cert_path, self.key_path, self.cert_sha256 = ensure_tls_identity(self.state_dir, ips)
        self.store = PairingStore(self.state_dir / "pairing.sqlite")
        self.coordinator = PairingCoordinator(self.store, self.cert_sha256)
        self.control_token = secrets.token_urlsafe(32)
        self.stop_event = threading.Event()

        self.lan_server = _QuietThreadingHTTPServer(
            (bind_host, port), _make_lan_handler(self.coordinator)
        )
        tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_ctx.load_cert_chain(certfile=str(self.cert_path), keyfile=str(self.key_path))
        self.lan_server.socket = tls_ctx.wrap_socket(self.lan_server.socket, server_side=True)

        self.control_server = _QuietThreadingHTTPServer(
            ("127.0.0.1", 0),
            _make_control_handler(self.coordinator, self.control_token, self.stop_event),
        )
        self.lan_port = int(self.lan_server.server_address[1])
        self.control_port = int(self.control_server.server_address[1])
        self._threads: list[threading.Thread] = []

    def start(self) -> dict[str, Any]:
        control_info = {
            "control_host": "127.0.0.1",
            "control_port": self.control_port,
            "control_token": self.control_token,
            "lan_host": self.bind_host,
            "lan_port": self.lan_port,
            "cert_sha256": self.cert_sha256,
            "protocol": PROTOCOL_VERSION,
        }
        control_file = self.state_dir / "control.json"
        control_file.write_text(json.dumps(control_info, indent=2) + "\n", encoding="utf-8")
        _chmod_0600(control_file)

        for srv in (self.lan_server, self.control_server):
            t = threading.Thread(
                target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
            )
            t.start()
            self._threads.append(t)
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "lan_host": self.bind_host,
            "lan_port": self.lan_port,
            "control_port": self.control_port,
            "cert_sha256": self.cert_sha256,
        }

    def stop(self) -> None:
        self.stop_event.set()
        for srv in (self.lan_server, self.control_server):
            srv.shutdown()
            srv.server_close()
        for t in self._threads:
            t.join(timeout=2.0)
        (self.state_dir / "control.json").unlink(missing_ok=True)


def pinned_https_request(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    expected_cert_sha256: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Perform an HTTPS request verifying the peer certificate's exact DER SHA-256 pin."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    raw_sock = socket.create_connection((host, port), timeout=timeout)
    tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
    try:
        der = tls_sock.getpeercert(binary_form=True)
        if not der:
            raise ssl.SSLError("Peer presented no certificate")
        actual_sha = hashlib.sha256(der).hexdigest()
        if not hmac.compare_digest(actual_sha, expected_cert_sha256.lower()):
            raise ssl.SSLError(
                f"Certificate pin mismatch: expected {expected_cert_sha256}, got {actual_sha}"
            )

        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        conn.sock = tls_sock
        req_headers = {"Host": f"{host}:{port}", **(headers or {})}
        payload = b""
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers["Content-Type"] = "application/json; charset=utf-8"
            req_headers["Content-Length"] = str(len(payload))
        conn.request(method, path, body=payload if body is not None else None, headers=req_headers)
        resp = conn.getresponse()
        raw_resp = resp.read()
        return resp.status, json.loads(raw_resp.decode("utf-8"))
    finally:
        tls_sock.close()


def _control_call(
    state_dir: Path, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    info = json.loads((state_dir / "control.json").read_text(encoding="utf-8"))
    conn = http.client.HTTPConnection(info["control_host"], info["control_port"], timeout=5.0)
    headers = {"X-Control-Token": info["control_token"]}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Foreground LAN pairing prototype CLI.")
    sub = parser.add_subparsers(dest="action", required=True)

    serve_p = sub.add_parser("serve", help="Run foreground HTTPS pairing endpoint.")
    serve_p.add_argument("--state-dir", type=Path, required=True)
    serve_p.add_argument("--bind", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=0)
    serve_p.add_argument("--san-ip", action="append", default=[])
    serve_p.add_argument("--open-pairing", action="store_true")

    open_p = sub.add_parser("open-pairing", help="Open a new single-use pairing session.")
    open_p.add_argument("--state-dir", type=Path, required=True)
    open_p.add_argument("--ttl", type=int, default=DEFAULT_PAIRING_TTL_SECONDS)

    app_p = sub.add_parser("approve", help="Approve a pending pairing request by SAS code.")
    app_p.add_argument("--state-dir", type=Path, required=True)
    app_p.add_argument("--request-id", required=True)
    app_p.add_argument("--sas-code", required=True)

    rev_p = sub.add_parser("revoke", help="Revoke a paired device immediately.")
    rev_p.add_argument("--state-dir", type=Path, required=True)
    rev_p.add_argument("--device-id", required=True)

    stat_p = sub.add_parser("status", help="Inspect paired devices and pending requests.")
    stat_p.add_argument("--state-dir", type=Path, required=True)

    stop_p = sub.add_parser("stop", help="Stop the running foreground server cleanly.")
    stop_p.add_argument("--state-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.action == "serve":
        srv = ForegroundPairingServer(
            args.state_dir, bind_host=args.bind, port=args.port, san_ips=args.san_ip
        )
        info = srv.start()
        if args.open_pairing:
            info["pairing_session"] = srv.coordinator.open_pairing_session()
        print(json.dumps(info, ensure_ascii=False), flush=True)

        def _on_signal(_signum: int, _frame: Any) -> None:
            srv.stop_event.set()

        signal.signal(signal.SIGINT, _on_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _on_signal)
        try:
            while not srv.stop_event.wait(timeout=0.25):
                time.sleep(0.0)
        finally:
            srv.stop()
        return 0

    if args.action == "open-pairing":
        code, res = _control_call(
            args.state_dir, "POST", "/control/pairing/open", {"ttl_seconds": args.ttl}
        )
    elif args.action == "approve":
        code, res = _control_call(
            args.state_dir,
            "POST",
            "/control/pairing/approve",
            {"request_id": args.request_id, "sas_code": args.sas_code},
        )
    elif args.action == "revoke":
        code, res = _control_call(
            args.state_dir, "POST", "/control/devices/revoke", {"device_id": args.device_id}
        )
    elif args.action == "status":
        code, res = _control_call(args.state_dir, "GET", "/control/status")
    else:
        code, res = _control_call(args.state_dir, "POST", "/control/stop", {})
    print(json.dumps(res, ensure_ascii=False))
    return 0 if code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
