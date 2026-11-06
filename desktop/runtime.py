"""Isolated D0 supervisor. Real application adapters and a GUI are not enabled yet."""

import hmac
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desktop.processes import OwnedProcess, OwnerExit

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = 1
_LAUNCHED = {}


def reap(folder, *, wait=False):
    retained = []
    for process in _LAUNCHED.pop(folder, []):
        if wait or process.poll() is not None:
            try:
                process.wait(timeout=5 if wait else 0)
            except subprocess.TimeoutExpired:
                retained.append(process)
        else:
            retained.append(process)
    if retained:
        _LAUNCHED[folder] = retained


class AlreadyRunning(RuntimeError):
    pass


def mode():
    if os.name == "nt":
        return "windows-native"
    return "wsl" if "microsoft" in platform.release().lower() else "linux"


def selection(database):
    return {
        "profile": "synthetic-probe",
        "mode": mode(),
        "project": str(ROOT),
        "backend_cwd": str(ROOT / "backend"),
        "database": str(Path(database).resolve()),
        "database_config_loaded": False,
        "database_opened": False,
    }


def prepare(folder):
    folder = Path(folder).resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def write_json(path, value):
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def instance_lock(folder):
    with open(folder / "manager.lock", "a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise AlreadyRunning("Runtime lock is held") from error
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AlreadyRunning("Runtime lock is held") from error
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(port, path, token="", method="GET"):
    # Do not send localhost control capabilities through user-configured proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        headers={"X-TravelFlow-Control": token},
        data=b"" if method == "POST" else None,
    )
    try:
        with opener.open(req, timeout=1) as response:
            return json.loads(response.read(64 * 1024))
    except urllib.error.HTTPError as error:
        error.close()
        raise


def record(folder):
    try:
        value = json.loads((folder / "runtime.json").read_text(encoding="utf-8"))
        if not isinstance(value.get("port"), int) or not 0 < value["port"] < 65536:
            return None
        if not isinstance(value.get("token"), str) or len(value["token"]) != 64:
            return None
        return value
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def current(folder):
    value = record(folder)
    if value:
        try:
            result = request(value["port"], "/status", value["token"])
            if isinstance(result, dict) and result.get("instance") == value.get("instance"):
                return result
        except (OSError, ValueError, urllib.error.URLError):
            pass
    return None


def free_ports(ports):
    reservations = []
    try:
        for port in ports:
            sock = socket.socket()
            reservations.append(sock)
            if os.name == "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", port))
    except OSError as error:
        raise RuntimeError(
            "Requested service port is occupied; no existing service was stopped"
        ) from error
    finally:
        for sock in reservations:
            sock.close()


def validate(folder, chosen, ports):
    if len(ports) != 2 or len(set(ports)) != 2 or not all(0 < p < 65536 for p in ports):
        raise ValueError("Two distinct explicit TCP ports are required")
    if not Path(chosen["database"]).is_relative_to(folder):
        raise ValueError(
            "The synthetic probe database descriptor must stay inside its isolated state directory"
        )
    # Refuse startup on kernels where a later stop could not confirm owner exit.
    capability = OwnerExit(os.getpid())
    capability.close()


def run_supervisor(folder, chosen, ports, *, timeout=8):
    validate(folder, chosen, ports)
    with instance_lock(folder):
        saved = folder / "selection.json"
        if saved.exists() and json.loads(saved.read_text(encoding="utf-8")) != chosen:
            raise RuntimeError(
                "Runtime mode/project/database selection differs; use a different state directory"
            )
        free_ports(ports)
        write_json(saved, chosen)
        instance = secrets.token_hex(16)
        token = secrets.token_hex(32)
        stopping = threading.Event()
        services = []
        health = []

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(2)
                super().setup()

            def handle_control(self):
                expected = f"127.0.0.1:{self.server.server_port}"
                origin = self.headers.get("Origin")
                if (
                    self.headers.get("Host") != expected
                    or (origin and origin != "http://" + expected)
                    or not hmac.compare_digest(
                        self.headers.get("X-TravelFlow-Control", "").encode("utf-8"),
                        token.encode("ascii"),
                    )
                ):
                    self.send_error(403)
                    return
                if self.command == "GET" and self.path == "/status":
                    value = {
                        "instance": instance,
                        "manager_pid": os.getpid(),
                        "protocol_version": PROTOCOL_VERSION,
                        "selection": chosen,
                        "services": health,
                        "ready": len(health) == len(ports)
                        and all(s.process.poll() is None for s in services),
                    }
                elif self.command == "POST" and self.path == "/stop":
                    stopping.set()
                    value = {"stopping": True, "instance": instance}
                else:
                    self.send_error(404)
                    return
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = handle_control
            do_POST = handle_control

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.timeout = 0.2
        try:
            with open(folder / "services.log", "ab") as log:
                env = {**os.environ, "TRAVELFLOW_PROBE_INSTANCE": instance}
                for port in ports:
                    service = OwnedProcess(
                        [
                            sys.executable,
                            str(Path(__file__).with_name("probe.py")),
                            str(port),
                        ],
                        cwd=ROOT / "backend",
                        env=env,
                        log=log,
                    )
                    services.append(service)
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        if service.process.poll() is not None:
                            raise RuntimeError("Owned probe process exited before becoming healthy")
                        try:
                            result = request(port, "/health")
                            if result.get("instance") == instance:
                                health.append({"port": port, **result})
                                break
                        except (OSError, ValueError, urllib.error.URLError):
                            pass
                        time.sleep(0.05)
                    else:
                        raise RuntimeError("Owned probe health identity was not confirmed")
                write_json(
                    folder / "runtime.json",
                    {"port": server.server_port, "token": token, "instance": instance},
                )
                while not stopping.is_set() and all(s.process.poll() is None for s in services):
                    server.handle_request()
        finally:
            server.server_close()
            for service in reversed(services):
                service.stop()
            value = record(folder)
            if value and value.get("instance") == instance:
                (folder / "runtime.json").unlink(missing_ok=True)


def start(folder, chosen, ports, *, timeout=12):
    validate(folder, chosen, ports)
    reap(folder)
    running = current(folder)
    if running:
        if running.get("protocol_version") != PROTOCOL_VERSION:
            raise RuntimeError("Use the matching supervisor version before reusing this instance")
        if running["selection"] != chosen or [s["port"] for s in running["services"]] != ports:
            raise RuntimeError("Another selection already owns this state directory")
        return {**running, "reused": True}
    command = [
        sys.executable,
        "-m",
        "desktop",
        "_serve",
        "--probe",
        "--state-dir",
        str(folder),
        "--database",
        chosen["database"],
        "--ports",
        *map(str, ports),
    ]
    with open(folder / "manager.log", "ab") as log:
        manager = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=os.name != "nt",
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    _LAUNCHED.setdefault(folder, []).append(manager)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = current(folder)
        if result:
            if result.get("protocol_version") != PROTOCOL_VERSION:
                raise RuntimeError(
                    "Use the matching supervisor version before reusing this instance"
                )
            if result["selection"] != chosen or [s["port"] for s in result["services"]] != ports:
                raise RuntimeError("Another selection won the startup lock")
            reused = result.get("manager_pid") != manager.pid
            if reused:
                # The losing launcher must release its log/lock handles before success.
                try:
                    manager.wait(timeout=3)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        "Concurrent launcher exit not confirmed; inspect status"
                    ) from error
                reap(folder)
            return {**result, "reused": reused}
        if manager.poll() not in (None, 2):
            raise RuntimeError(
                "Supervisor did not start; inspect manager.log in the isolated state directory"
            )
        time.sleep(0.05)
    # Do not force-kill a manager and orphan its services. Let its startup timeout clean up.
    raise RuntimeError("Supervisor startup not confirmed; inspect status before retrying")


def stop(folder):
    value = record(folder)
    if not value:
        reap(folder)
        return {"stopped": True, "was_running": False}
    owner_status = current(folder)
    if not owner_status:
        raise RuntimeError("Owner is not verified; state retained and no PID was terminated")
    if (
        owner_status.get("protocol_version") != PROTOCOL_VERSION
        or not isinstance(owner_status.get("manager_pid"), int)
        or owner_status["manager_pid"] <= 1
    ):
        raise RuntimeError("Unsupported supervisor protocol; use its matching version to stop it")
    owner = OwnerExit(owner_status["manager_pid"])
    try:
        request(value["port"], "/stop", value["token"], "POST")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not (folder / "runtime.json").exists():
                owner.wait(max(0, deadline - time.monotonic()))
                reap(folder, wait=True)
                return {"stopped": True, "was_running": True}
            time.sleep(0.05)
        raise RuntimeError("Stop not confirmed; state retained for diagnosis")
    finally:
        owner.close()
