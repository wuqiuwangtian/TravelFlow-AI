"""Managed isolated-trip LAN sync controller for the desktop management console.

Reuses desktop.lan_pairing's loopback control plane (/control/*) and
backend/scripts/exchange_trip_handoff.py without introducing non-stdlib
dependencies into the console process or touching personal databases / .env.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from desktop.application import python_path as desktop_backend_python
from desktop.runtime import ROOT, mode, prepare, write_json

DEFAULT_PAIRING_TTL_SECONDS = 120
FICTIONAL_TRIP_TITLE = "北京三日游"
_PAIRING_ERROR_MESSAGES = {
    "sas_mismatch": "核对短码不匹配，请重新确认手机屏幕上的 6 位短码。",
    "request_not_found": "未找到该待审配对请求，可能已过期或服务已重启。",
    "pairing_session_unavailable": "本次配对会话已失效或超时，请重新生成配对资料。",
    "active_device_not_found": "该设备尚未完成配对或已被撤销，无法授权旅行。",
    "invalid_grant": "旅行授权参数无效，请核对业务设备标识。",
    "unauthorized": "本地同步控制凭据校验失败，请重新启动同步服务。",
}


def sync_backend_python() -> Path | None:
    """Locate a backend Python 3.13+ interpreter without importing backend packages."""
    override = os.environ.get("TRAVELFLOW_SYNC_PYTHON")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None

    rel_bin = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    for candidate in (
        desktop_backend_python(),
        ROOT / "backend" / ".venv" / rel_bin,
    ):
        if candidate.is_file():
            return candidate
    return None


def detect_bind_addresses() -> list[str]:
    """Return loopback plus detected non-link-local IPv4 addresses for binding."""
    found: list[str] = []

    def _add(raw: str) -> None:
        try:
            addr = ipaddress.IPv4Address(raw)
        except ValueError:
            return
        if addr.is_unspecified or addr.is_link_local or addr.is_multicast:
            return
        text = str(addr)
        if text not in found:
            found.append(text)

    # Prefer the primary outbound LAN IPv4 first (no packets transmitted on UDP connect).
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            _add(sock.getsockname()[0])
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(str(info[4][0]))
    except OSError:
        pass

    _add("127.0.0.1")
    return found


def _validate_ipv4(raw: Any, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"请填写有效的 IPv4 {field_name}。")
    text = raw.strip()
    try:
        addr = ipaddress.IPv4Address(text)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是有效的 IPv4 地址。") from exc
    if addr.is_unspecified:
        raise ValueError(
            f"{field_name}不能使用 0.0.0.0，请选择明确地址（如本机局域网 IP 或 127.0.0.1）。"
        )
    return str(addr)


def _parse_san_ips(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        tokens = [str(item).strip() for item in raw if str(item).strip()]
    elif isinstance(raw, str):
        tokens = [
            tok.strip()
            for tok in raw.replace("，", ",").replace(" ", ",").split(",")
            if tok.strip()
        ]
    else:
        raise ValueError("附加证书 IP 必须是文本或列表。")
    result: list[str] = []
    for token in tokens:
        validated = _validate_ipv4(token, field_name="附加证书 IP")
        if validated not in result:
            result.append(validated)
    return result


def _validate_port(raw: Any) -> int:
    if raw is None or raw == "":
        return 0
    if type(raw) is bool:
        raise ValueError("同步端口必须是 0 或 1–65535 之间的整数。")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw.isdigit():
            raise ValueError("同步端口必须是 0 或 1–65535 之间的整数。")
        port = int(raw)
    elif isinstance(raw, int):
        port = raw
    else:
        raise ValueError("同步端口必须是 0 或 1–65535 之间的整数。")
    if not 0 <= port <= 65535:
        raise ValueError("同步端口必须是 0 或 1–65535 之间的整数。")
    return port


def _translate_pairing_error(code: int, body: dict[str, Any]) -> str:
    err = str(body.get("error") or "")
    if err in _PAIRING_ERROR_MESSAGES:
        return _PAIRING_ERROR_MESSAGES[err]
    if err.startswith("invalid_state:"):
        return f"当前配对请求状态不可执行该操作（{err.split(':', 1)[1]}）。"
    return f"同步控制操作失败（HTTP {code}：{err or 'unknown'}）。"


def _read_cert_sha256(cert_path: Path) -> str | None:
    if not cert_path.is_file():
        return None
    try:
        der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="utf-8"))
        return hashlib.sha256(der).hexdigest()
    except (OSError, ValueError):
        return None


def _read_persisted_pairing_db(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not db_path.is_file():
        return [], []
    try:
        with closing(
            sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
        ) as db:
            db.row_factory = sqlite3.Row
            devices = [
                dict(row)
                for row in db.execute(
                    "SELECT device_id, device_name, status, paired_at, revoked_at "
                    "FROM paired_devices ORDER BY paired_at, device_id"
                ).fetchall()
            ]
            grants = [
                dict(row)
                for row in db.execute(
                    "SELECT paired_device_id, dataset_id, source_device_id, travel_id, "
                    "branch_id, can_write, can_lookup_receipt, granted_at "
                    "FROM device_exchange_grants ORDER BY granted_at, paired_device_id"
                ).fetchall()
            ]
            return devices, grants
    except sqlite3.Error:
        return [], []


def _read_trip_ledger_summary(trip_db: Path, branch_id: str) -> dict[str, Any] | None:
    if not trip_db.is_file():
        return None
    try:
        with closing(
            sqlite3.connect(f"{trip_db.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
        ) as db:
            db.row_factory = sqlite3.Row
            head = db.execute(
                "SELECT v.id, p.itinerary FROM versions v "
                "JOIN plans p ON p.version_id = v.id "
                "WHERE v.branch_id = ? ORDER BY v.seq DESC LIMIT 1",
                (branch_id,),
            ).fetchone()
            if head is None:
                return None
            expenses = (json.loads(head["itinerary"] or "{}")).get("expenses") or []
            paid_cents = sum(int(item.get("amount_cents", 0)) for item in expenses)
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            }
            pending_outbound = 0
            confirmed_outbound = 0
            if "exchange_outbound_operations" in tables:
                rows = db.execute(
                    "SELECT delivery_state FROM exchange_outbound_operations WHERE branch_id = ?",
                    (branch_id,),
                ).fetchall()
                pending_outbound = sum(1 for r in rows if r["delivery_state"] != "confirmed")
                confirmed_outbound = sum(1 for r in rows if r["delivery_state"] == "confirmed")
            return {
                "head_revision_id": str(head["id"]),
                "expense_count": len(expenses),
                "paid_cents": paid_cents,
                "pending_outbound_count": pending_outbound,
                "confirmed_outbound_count": confirmed_outbound,
            }
    except (sqlite3.Error, ValueError, TypeError):
        return None


class LanSyncManager:
    """Manage the isolated-trip foreground lan_pairing server from desktop.console."""

    def __init__(self, console_folder: Path) -> None:
        self.root = prepare(console_folder / "lan-sync")
        self.trip_dir = self.root / "trip"
        self.trip_db = self.trip_dir / "travel.sqlite"
        self.context_path = self.root / "context.json"
        self.pairing_dir = self.root / "pairing-state"
        self.config_path = self.root / "sync-config.json"
        self.log_path = self.root / "sync.log"
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._latest_pairing: dict[str, Any] | None = None

    def _load_config(self) -> dict[str, Any]:
        candidates = detect_bind_addresses()
        default_host = candidates[0] if (mode() == "windows-native" and candidates) else "127.0.0.1"
        default = {"bind_host": default_host, "port": 0, "san_ips": []}
        if not self.config_path.is_file():
            return default
        try:
            saved = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                return default
            return {
                "bind_host": _validate_ipv4(
                    saved.get("bind_host", default_host), field_name="监听地址"
                ),
                "port": _validate_port(saved.get("port", 0)),
                "san_ips": _parse_san_ips(saved.get("san_ips", [])),
            }
        except (OSError, ValueError, TypeError):
            return default

    def _load_context(self) -> dict[str, Any] | None:
        if not self.context_path.is_file():
            return None
        try:
            data = json.loads(self.context_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _control_call(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: float = 4.0
    ) -> tuple[int, dict[str, Any]] | None:
        control_file = self.pairing_dir / "control.json"
        if not control_file.is_file():
            return None
        try:
            info = json.loads(control_file.read_text(encoding="utf-8"))
            host = str(info["control_host"])
            port = int(info["control_port"])
            token = str(info["control_token"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if host != "127.0.0.1" or not 0 < port < 65536 or not token:
            return None

        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        headers = {"X-Control-Token": token}
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(256 * 1024)
            return resp.status, json.loads(raw.decode("utf-8"))
        except (OSError, http.client.HTTPException, ValueError):
            return None
        finally:
            conn.close()

    def _require_live_control(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        reply = self._control_call(method, path, body)
        if reply is None:
            raise RuntimeError("同步服务未运行，请先启动同步服务。")
        code, data = reply
        if code != 200 or not data.get("ok"):
            raise ValueError(_translate_pairing_error(code, data))
        return data

    def _build_pairing_bundle(
        self,
        *,
        protocol: str,
        lan_host: str,
        lan_port: int,
        session: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # Strictly exclude control_port / control_token from phone-facing pairing data.
        bundle: dict[str, Any] = {
            "protocol": protocol,
            "lan_host": lan_host,
            "lan_port": int(lan_port),
            "cert_sha256": str(session["cert_sha256"]),
            "pairing_session_id": str(session["pairing_session_id"]),
            "pairing_code": str(session["pairing_code"]),
            "expires_at": str(session["expires_at"]),
            "pairing_session": {
                "pairing_session_id": str(session["pairing_session_id"]),
                "pairing_code": str(session["pairing_code"]),
                "cert_sha256": str(session["cert_sha256"]),
                "expires_at": str(session["expires_at"]),
            },
        }
        if context is not None:
            bundle["trip"] = {
                "title": FICTIONAL_TRIP_TITLE,
                "dataset_id": str(context["dataset_id"]),
                "travel_id": str(context["travel_id"]),
                "branch_id": str(context["branch_id"]),
                "current_revision_id": str(context["current_revision_id"]),
            }
        return bundle

    def _ensure_isolated_trip(self, backend_py: Path) -> dict[str, Any]:
        existing = self._load_context()
        if self.trip_db.is_file() and existing is not None:
            return existing

        handoff_script = ROOT / "backend" / "scripts" / "exchange_trip_handoff.py"
        if self.trip_dir.exists() and not self.trip_db.is_file():
            raise RuntimeError(f"隔离测试旅行目录不完整，请手动清理后重试：{self.trip_dir}")
        proc = subprocess.run(
            [
                str(backend_py),
                str(handoff_script),
                "init-trip",
                "--work-dir",
                str(self.trip_dir),
                "--context-out",
                str(self.context_path),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=45.0,
            check=False,
        )
        if proc.returncode != 0:
            detail = (
                proc.stderr.decode("utf-8", errors="replace").strip()
                or proc.stdout.decode("utf-8", errors="replace").strip()
            )
            raise RuntimeError(f"初始化隔离测试旅行失败：{detail[:240]}")
        ctx = self._load_context()
        if ctx is None or not self.trip_db.is_file():
            raise RuntimeError("隔离测试旅行初始化完成但上下文文件缺失。")
        return ctx

    def start(self, *, bind_host: Any, port: Any = 0, san_ips: Any = None) -> dict[str, Any]:
        host = _validate_ipv4(bind_host, field_name="监听地址")
        port_num = _validate_port(port)
        extra_sans = _parse_san_ips(san_ips)

        live = self._control_call("GET", "/control/status")
        if live is not None and live[0] == 200 and live[1].get("ok"):
            raise ValueError("同步服务已在运行；如需更改监听地址或端口，请先停止服务。")

        backend_py = sync_backend_python()
        if backend_py is None:
            raise RuntimeError(
                "未找到后端 Python 3.13+ 虚拟环境；请先点击「准备环境与构建」，"
                "或通过 TRAVELFLOW_SYNC_PYTHON 指定后端解释器。"
            )

        ctx = self._ensure_isolated_trip(backend_py)
        prepare(self.pairing_dir)
        cmd = [
            str(backend_py),
            "-m",
            "desktop.lan_pairing",
            "serve",
            "--state-dir",
            str(self.pairing_dir),
            "--bind",
            host,
            "--port",
            str(port_num),
            "--trip-db",
            str(self.trip_db),
            "--open-pairing",
        ]
        for ip in extra_sans:
            cmd.extend(["--san-ip", ip])

        with open(self.log_path, "ab") as log_handle:
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log_handle,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        assert proc.stdout is not None
        first_line: list[bytes] = []

        def _read_first() -> None:
            first_line.append(proc.stdout.readline())  # type: ignore[union-attr]

        reader = threading.Thread(target=_read_first, daemon=True)
        reader.start()
        reader.join(timeout=30.0)
        if reader.is_alive() or not first_line or not first_line[0]:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            raise RuntimeError(f"同步服务启动超时或异常退出，请查看日志：{self.log_path}")

        try:
            info = json.loads(first_line[0].decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            if proc.poll() is None:
                proc.terminate()
            raise RuntimeError("同步服务返回了无法解析的启动信息。") from exc

        def _drain_stdout() -> None:
            assert proc.stdout is not None
            try:
                with open(self.log_path, "ab") as sink:
                    for chunk in iter(lambda: proc.stdout.read(4096), b""):
                        sink.write(chunk)
            except OSError:
                pass

        threading.Thread(target=_drain_stdout, daemon=True).start()

        write_json(self.config_path, {"bind_host": host, "port": port_num, "san_ips": extra_sans})
        session = info.get("pairing_session")
        bundle = (
            self._build_pairing_bundle(
                protocol=str(info.get("protocol", "travelflow-lan-pairing/0-draft")),
                lan_host=str(info["lan_host"]),
                lan_port=int(info["lan_port"]),
                session=session,
                context=ctx,
            )
            if isinstance(session, dict)
            else None
        )
        with self._lock:
            self._proc = proc
            self._latest_pairing = bundle
        return {"lan_host": str(info["lan_host"]), "lan_port": int(info["lan_port"])}

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._latest_pairing = None
        reply = self._control_call("POST", "/control/stop", {})
        deadline = time.monotonic() + 12.0
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=max(1.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5.0)
        elif reply is not None:
            while time.monotonic() < deadline:
                if self._control_call("GET", "/control/status", timeout=1.0) is None:
                    break
                time.sleep(0.1)
        with self._lock:
            if self._proc is proc:
                self._proc = None

    def open_pairing(self, *, ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS) -> dict[str, Any]:
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 600:
            raise ValueError("配对有效期需在 30 至 600 秒之间。")
        data = self._require_live_control(
            "POST", "/control/pairing/open", {"ttl_seconds": ttl_seconds}
        )
        session = data.get("pairing_session") or {
            "pairing_session_id": data["pairing_session_id"],
            "pairing_code": data["pairing_code"],
            "cert_sha256": data["cert_sha256"],
            "expires_at": data["expires_at"],
        }
        bundle = self._build_pairing_bundle(
            protocol=str(data.get("protocol", "travelflow-lan-pairing/0-draft")),
            lan_host=str(data["lan_host"]),
            lan_port=int(data["lan_port"]),
            session=session,
            context=self._load_context(),
        )
        with self._lock:
            self._latest_pairing = bundle
        return bundle

    def approve(self, *, request_id: Any, sas_code: Any) -> dict[str, Any]:
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            raise ValueError("缺少有效的配对请求标识。")
        if (
            not isinstance(sas_code, str)
            or len(sas_code.strip()) != 6
            or not sas_code.strip().isdigit()
        ):
            raise ValueError("核对短码必须是 6 位数字。")
        return self._require_live_control(
            "POST",
            "/control/pairing/approve",
            {"request_id": request_id.strip(), "sas_code": sas_code.strip()},
        )

    def reject(self, *, request_id: Any) -> dict[str, Any]:
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            raise ValueError("缺少有效的配对请求标识。")
        return self._require_live_control(
            "POST", "/control/pairing/reject", {"request_id": request_id.strip()}
        )

    def authorize_trip(self, *, device_id: Any, source_device_id: Any) -> dict[str, Any]:
        if not isinstance(device_id, str) or not 1 <= len(device_id.strip()) <= 128:
            raise ValueError("缺少有效的已配对设备标识。")
        if not isinstance(source_device_id, str) or not 1 <= len(source_device_id.strip()) <= 256:
            raise ValueError("请填写业务设备标识（source_device_id）。")
        ctx = self._load_context()
        if ctx is None:
            raise RuntimeError("尚未初始化隔离测试旅行，请先启动同步服务。")
        return self._require_live_control(
            "POST",
            "/control/devices/bind-exchange",
            {
                "device_id": device_id.strip(),
                "dataset_id": str(ctx["dataset_id"]),
                "source_device_id": source_device_id.strip(),
                "travel_id": str(ctx["travel_id"]),
                "branch_id": str(ctx["branch_id"]),
                "can_write": True,
                "can_lookup_receipt": True,
            },
        )

    def revoke(self, *, device_id: Any) -> dict[str, Any]:
        if not isinstance(device_id, str) or not 1 <= len(device_id.strip()) <= 128:
            raise ValueError("缺少有效的设备标识。")
        return self._require_live_control(
            "POST", "/control/devices/revoke", {"device_id": device_id.strip()}
        )

    def snapshot(self) -> dict[str, Any]:
        cfg = self._load_config()
        ctx = self._load_context()
        candidates = detect_bind_addresses()
        backend_py = sync_backend_python()
        live_reply = self._control_call("GET", "/control/status", timeout=2.0)

        trip_info: dict[str, Any] | None = None
        if ctx is not None:
            ledger = _read_trip_ledger_summary(self.trip_db, str(ctx["branch_id"]))
            trip_info = {
                "title": FICTIONAL_TRIP_TITLE,
                "dataset_id": str(ctx["dataset_id"]),
                "travel_id": str(ctx["travel_id"]),
                "branch_id": str(ctx["branch_id"]),
                "fork_branch_id": str(ctx.get("fork_branch_id") or ""),
                "database": str(self.trip_db),
                "head_revision_id": (
                    ledger["head_revision_id"] if ledger else str(ctx["current_revision_id"])
                ),
                "expense_count": ledger["expense_count"] if ledger else None,
                "paid_cents": ledger["paid_cents"] if ledger else None,
                "pending_outbound_count": ledger["pending_outbound_count"] if ledger else 0,
                "confirmed_outbound_count": ledger["confirmed_outbound_count"] if ledger else 0,
            }

        if live_reply is not None and live_reply[0] == 200 and live_reply[1].get("ok"):
            status = live_reply[1]
            lan_host = str(status.get("lan_host") or cfg["bind_host"])
            lan_port = int(status.get("lan_port") or 0)
            cert_sha = str(status.get("cert_sha256") or "")
            with self._lock:
                bundle = self._latest_pairing
                if bundle is not None:
                    expired = False
                    try:
                        exp_dt = datetime.fromisoformat(str(bundle["expires_at"]))
                        expired = datetime.now(UTC) >= exp_dt
                    except ValueError:
                        expired = True
                    if (
                        expired
                        or bundle.get("cert_sha256") != cert_sha
                        or int(bundle.get("lan_port") or 0) != lan_port
                    ):
                        self._latest_pairing = None
                        bundle = None
                    elif trip_info is not None and isinstance(bundle.get("trip"), dict):
                        bundle = {
                            **bundle,
                            "trip": {
                                **bundle["trip"],
                                "current_revision_id": trip_info["head_revision_id"],
                            },
                        }
            return {
                "running": True,
                "windows_native": mode() == "windows-native",
                "backend_python_ready": backend_py is not None,
                "state_dir": str(self.pairing_dir),
                "bind_candidates": candidates,
                "config": cfg,
                "lan_host": lan_host,
                "lan_port": lan_port,
                "cert_sha256": cert_sha,
                "exchange_enabled": bool(status.get("exchange_enabled")),
                "pairing": bundle,
                "pairing_json": json.dumps(bundle, ensure_ascii=False, indent=2)
                if bundle
                else None,
                "pending_requests": list(status.get("pending_requests") or []),
                "devices": list(status.get("devices") or []),
                "exchange_grants": list(status.get("exchange_grants") or []),
                "trip": trip_info,
            }

        with self._lock:
            self._latest_pairing = None
        devices, grants = _read_persisted_pairing_db(self.pairing_dir / "pairing.sqlite")
        return {
            "running": False,
            "windows_native": mode() == "windows-native",
            "backend_python_ready": backend_py is not None,
            "state_dir": str(self.pairing_dir),
            "bind_candidates": candidates,
            "config": cfg,
            "lan_host": None,
            "lan_port": None,
            "cert_sha256": _read_cert_sha256(self.pairing_dir / "server.crt"),
            "exchange_enabled": False,
            "pairing": None,
            "pairing_json": None,
            "pending_requests": [],
            "devices": devices,
            "exchange_grants": grants,
            "trip": trip_info,
        }
