"""Local source-mode management UI, independent of Next and backend dependencies."""

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desktop.application import application_selection, doctor, prepare_application
from desktop.processes import OwnerExit, supervisor_python
from desktop.runtime import (
    ROOT,
    AlreadyRunning,
    NoRedirect,
    current,
    file_lock,
    identity,
    mode,
    prepare,
    start,
    stop,
    write_json,
)

ASSETS = Path(__file__).with_name("ui")
_LAUNCHED = []


def read_record(folder):
    try:
        value = json.loads((folder / "console.json").read_text(encoding="utf-8"))
        if not (isinstance(value["port"], int) and 0 < value["port"] < 65536):
            return None
        if len(value["token"]) != 64 or len(value["instance"]) != 32:
            return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def console_request(record, path="/api/ping", payload=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{record['port']}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"X-TravelFlow-Control": record["token"], "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=3) as response:
        return json.loads(response.read(64 * 1024))


def live_record(folder):
    record = read_record(folder)
    if record:
        try:
            if console_request(record).get("instance") == record["instance"]:
                return record
        except (OSError, ValueError, urllib.error.URLError):
            pass
    return None


class Console:
    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.Lock()
        self.exiting = threading.Event()
        self.busy = None
        self.exit_ready = False
        self.error = None
        self.preview = None
        self.confirmation = None
        self.preferences = {}
        self.active = None
        try:
            saved = json.loads((folder / "preferences.json").read_text(encoding="utf-8"))
            self.preferences = saved["preferences"]
            active = Path(saved["active"]).resolve()
            if active.is_relative_to(folder):
                self.active = active
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def snapshot(self):
        with self.lock:
            data = {
                "mode": mode(),
                "project": str(ROOT),
                "state_dir": str(self.folder),
                "busy": self.busy,
                "exit_ready": self.exit_ready,
                "error": self.error,
                "preview": self.preview,
                "preferences": self.preferences,
            }
            active = self.active
        data["runtime"] = current(active) if active else None
        data["runtime_unverified"] = bool(
            active and (active / "runtime.json").exists() and not data["runtime"]
        )
        return data

    def inspect(self, payload):
        profile = payload.get("profile")
        if profile not in ("application", "application-qa"):
            raise ValueError("请选择真实应用或隔离体验。")
        ports = payload.get("ports")
        if (
            not isinstance(ports, list)
            or len(ports) != 2
            or any(type(p) is not int or not 0 < p < 65536 for p in ports)
            or ports[0] == ports[1]
        ):
            raise ValueError("请输入两个不同的有效端口。")
        env = payload.get("env_file", "")
        database = payload.get("database", "")
        if not isinstance(env, str) or not isinstance(database, str):
            raise ValueError("配置和数据库地址必须是文本。")
        if profile == "application-qa" and (env or database):
            raise ValueError("隔离体验使用自己的配置和数据，请清空自定义地址。")
        chosen = application_selection(
            self.folder / "qa",
            profile,
            Path(database) if database else None,
            Path(env) if env else None,
        )
        info = doctor()
        chosen["frontend_build_id"] = info["build_id"]
        digest = hashlib.sha256(json.dumps(identity(chosen), sort_keys=True).encode()).hexdigest()
        runtime_dir = (
            self.folder / "qa"
            if profile == "application-qa"
            else self.folder / "instances" / digest
        )
        exists = Path(chosen["database"]).is_file()
        if Path(chosen["database"]).exists() and not exists:
            raise ValueError("数据库地址指向了目录，请选择文件。")
        fingerprint = (
            hashlib.sha256(Path(chosen["env_file"]).read_bytes()).hexdigest()
            if profile == "application"
            else None
        )
        public = {
            "selection": chosen,
            "ports": ports,
            "database_exists": exists,
            "preparation": info,
            "confirmation": secrets.token_hex(16),
        }
        with self.lock:
            self.preview = public
            self.confirmation = (dict(payload), runtime_dir, fingerprint)

    def launch(self, payload):
        with self.lock:
            preview, confirmation = self.preview, self.confirmation
        if not preview or payload.get("confirmation") != preview["confirmation"]:
            raise ValueError("请先预览并确认本次配置和数据地址。")
        settings, folder, fingerprint = confirmation
        chosen = dict(preview["selection"])
        if (
            fingerprint
            and hashlib.sha256(Path(chosen["env_file"]).read_bytes()).hexdigest() != fingerprint
        ):
            raise ValueError("配置文件已变化，请重新预览。")
        if doctor()["build_id"] != chosen["frontend_build_id"]:
            raise ValueError("应用构建已变化，请重新预览。")
        if chosen["profile"] == "application" and not Path(chosen["database"]).is_file():
            if payload.get("initialize_database") is not True:
                raise ValueError("数据库不存在；核对预览后，勾选明确创建新数据库。")
            chosen["initialize_database"] = True
        if self.active and self.active != folder and current(self.active):
            raise ValueError("请先停止当前应用，再切换配置或数据。")
        # Persist the target before launch so a failed/slow startup remains stoppable on reopen.
        prepare(folder)
        with self.lock:
            self.active = folder
            self.preferences = settings
        write_json(
            self.folder / "preferences.json", {"preferences": settings, "active": str(folder)}
        )
        start(folder, chosen, preview["ports"])

    def stop_application(self):
        if self.active:
            if (self.active / "runtime.json").exists() and not current(self.active):
                raise RuntimeError(
                    "已有实例状态无法确认；保留现场，请核对运行日志，不会按旧PID终止进程。"
                )
            stop(self.active)

    def submit(self, action, payload):
        if action not in ("inspect", "prepare", "start", "stop", "exit"):
            raise ValueError("未知操作。")
        with self.lock:
            if self.busy or self.exit_ready or self.exiting.is_set():
                raise AlreadyRunning("操作正在进行，请等待完成。")
            self.busy, self.error = action, None
            if action == "inspect":
                self.preview = self.confirmation = None

        def work():
            try:
                if action == "inspect":
                    self.inspect(payload)
                elif action == "prepare":
                    prepare_application()
                    with self.lock:
                        self.preview = self.confirmation = None
                elif action == "start":
                    self.launch(payload)
                else:
                    self.stop_application()
                    if action == "exit":
                        self.exit_ready = True
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                with self.lock:
                    self.error = str(error)
            finally:
                with self.lock:
                    self.busy = None

        threading.Thread(target=work, daemon=True).start()


def serve_console(folder):
    with file_lock(folder / "console.lock"):
        state = Console(folder)
        token, instance = secrets.token_hex(32), secrets.token_hex(16)

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(3)
                super().setup()

            def send(self, code, body, content_type="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

            def allowed(self):
                expected = f"127.0.0.1:{self.server.server_port}"
                return self.headers.get("Host") == expected and self.headers.get("Origin") in (
                    None,
                    "http://" + expected,
                )

            def handle_request(self):
                closing = False
                if not self.allowed():
                    self.send(403, b"{}")
                    return
                assets = {
                    "/": (ASSETS / "index.html", "text/html; charset=utf-8"),
                    "/console.js": (ASSETS / "console.js", "text/javascript; charset=utf-8"),
                    "/console.css": (ASSETS / "console.css", "text/css; charset=utf-8"),
                    "/tokens.css": (ROOT / "frontend/styles/tokens.css", "text/css; charset=utf-8"),
                }
                if self.command == "GET" and self.path in assets:
                    path, mime = assets[self.path]
                    self.send(200, path.read_bytes(), mime)
                    return
                supplied = self.headers.get("X-TravelFlow-Control", "").encode()
                if not hmac.compare_digest(supplied, token.encode()):
                    self.send(403, b"{}")
                    return
                if self.command == "GET" and self.path == "/api/ping":
                    result = {"instance": instance, "pid": os.getpid()}
                elif self.command == "GET" and self.path == "/api/state":
                    result = state.snapshot()
                elif self.command == "POST" and self.path == "/api/close":
                    if not state.exit_ready:
                        self.send(409, b"{}")
                        return
                    result = {"closing": True}
                    closing = True
                elif self.command == "POST" and self.path.startswith("/api/"):
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if (
                            not 0 < length <= 16384
                            or self.headers.get("Content-Type") != "application/json"
                        ):
                            raise ValueError("请求格式不正确。")
                        payload = json.loads(self.rfile.read(length))
                        if not isinstance(payload, dict):
                            raise ValueError("请求必须是对象。")
                        state.submit(self.path[5:], payload)
                        result = {"accepted": True}
                    except AlreadyRunning as error:
                        self.send(409, json.dumps({"error": str(error)}).encode())
                        return
                    except (ValueError, TypeError) as error:
                        self.send(400, json.dumps({"error": str(error)}).encode())
                        return
                else:
                    self.send(404, b"{}")
                    return
                self.send(200, json.dumps(result).encode())
                if closing:
                    self.wfile.flush()
                    state.exiting.set()

            do_GET = handle_request
            do_POST = handle_request

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Drain active HTTP responses before the process exits, including /api/close.
        server.daemon_threads = False
        server.timeout = 0.2
        write_json(
            folder / "console.json",
            {"port": server.server_port, "token": token, "instance": instance},
        )
        try:
            while not state.exiting.is_set():
                server.handle_request()
        finally:
            server.server_close()
            (folder / "console.json").unlink(missing_ok=True)


def open_console(folder, *, browser=True):
    for child in _LAUNCHED[:]:
        if child.poll() is not None:
            child.wait()
            _LAUNCHED.remove(child)
    folder = prepare(folder)
    existing = live_record(folder)
    process = None
    if not existing:
        with open(folder / "console.log", "ab") as log:
            process = subprocess.Popen(
                [supervisor_python(), "-m", "desktop", "_console", "--state-dir", str(folder)],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        _LAUNCHED.append(process)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            existing = live_record(folder)
            if existing:
                if process.pid != console_request(existing)["pid"]:
                    process.wait(timeout=3)
                break
            if process.poll() not in (None, 2):
                raise RuntimeError("管理页面启动失败，请查看状态目录中的console.log。")
            time.sleep(0.05)
        else:
            raise RuntimeError("管理页面尚未就绪，请查看console.log后重试。")
    if browser:
        url = f"http://127.0.0.1:{existing['port']}/#{existing['token']}"
        if not webbrowser.open(url):
            raise RuntimeError("无法打开默认浏览器；管理器已运行，请配置本机浏览器后重新打开入口。")
    return existing


def close_console(folder):
    """Authenticated shutdown and kernel-exit confirmation, used by acceptance tooling."""
    record = live_record(folder)
    if not record:
        return
    owner = OwnerExit(console_request(record)["pid"])
    try:
        if not console_request(record, "/api/state")["exit_ready"]:
            console_request(record, "/api/exit", {})
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            snapshot = console_request(record, "/api/state")
            if snapshot["error"]:
                raise RuntimeError(snapshot["error"])
            if snapshot["exit_ready"]:
                console_request(record, "/api/close", {})
                owner.wait(max(0, deadline - time.monotonic()))
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("管理器退出未确认，请重新打开入口核对。")
    finally:
        owner.close()
        for child in _LAUNCHED[:]:
            if child.poll() is not None:
                child.wait()
                _LAUNCHED.remove(child)
