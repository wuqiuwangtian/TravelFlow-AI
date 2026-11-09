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
from desktop.host_ports import ensure_host_ports
from desktop.processes import OwnerExit, supervisor_python
from desktop.runtime import (
    ROOT,
    AlreadyRunning,
    NoRedirect,
    archive_metadata,
    current,
    file_lock,
    identity,
    lock_held,
    mode,
    prepare,
    start,
    stop,
    unconfirmed,
    write_json,
)
from desktop.runtime import (
    recover as recover_runtime,
)

CONSOLE_VERSION = 5
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
        self.picked = None
        self.confirmation = None
        self.preferences = {}
        self.active = None
        self.state_error = None
        self.recovery = None
        path = folder / "preferences.json"
        if path.exists() or path.is_symlink():
            try:
                if path.is_symlink():
                    raise ValueError("Unexpected preferences link")
                saved = json.loads(path.read_text(encoding="utf-8"))
                settings, active = saved["preferences"], Path(saved["active"])
                if (
                    not isinstance(settings, dict)
                    or settings.get("profile") not in ("application", "application-qa")
                    or not isinstance(settings.get("ports"), list)
                    or len(settings["ports"]) != 2
                    or len(set(settings["ports"])) != 2
                    or any(
                        type(port) is not int or not 0 < port < 65536 for port in settings["ports"]
                    )
                    or any(
                        not isinstance(settings.get(key, ""), str)
                        for key in ("env_file", "database")
                    )
                    or not active.is_absolute()
                    or not active.resolve().is_relative_to(folder)
                    or active.resolve() == folder
                ):
                    raise ValueError("Invalid preferences")
                self.preferences = {
                    key: settings[key]
                    for key in ("profile", "ports", "env_file", "database")
                    if key in settings
                }
                self.active = active.resolve()
            except (OSError, ValueError, KeyError, TypeError):
                self.state_error = (
                    "上次运行选择无法读取或不完整；已保留原文件，请先核对并恢复运行状态。"
                )
        if not self.state_error:
            try:
                if any(
                    candidate != self.active and unconfirmed(candidate)
                    for candidate in self.candidates()
                ):
                    self.state_error = "发现尚未接续的实例状态；请先核对并恢复，不会自动打开新库。"
            except (OSError, ValueError, RuntimeError):
                self.state_error = "实例状态目录异常；已保留现场，请核对后恢复。"

    def candidates(self):
        folders = [self.folder / "qa"]
        parent = self.folder / "instances"
        if not parent.resolve().is_relative_to(self.folder):
            raise RuntimeError("实例目录指向管理范围之外；请保留现场并核对路径。")
        if parent.is_dir():
            folders += list(parent.iterdir())
        if self.active:
            folders.append(self.active)
        result = set()
        for path in folders:
            if path.is_dir():
                if not path.resolve().is_relative_to(self.folder) or path.resolve() == self.folder:
                    raise RuntimeError("实例路径不在管理范围内；不会读取或接管它。")
                result.add(path.resolve())
        return sorted(result)

    def recover(self):
        live, stale = [], []
        for folder in self.candidates():
            value = current(folder)
            if value:
                chosen = value.get("selection", {})
                if (
                    chosen.get("project") != str(ROOT)
                    or chosen.get("mode") != mode()
                    or chosen.get("profile") not in ("application", "application-qa")
                ):
                    raise RuntimeError("实例归属不符，保留状态，不会自动接管。")
                live.append((folder, value))
            elif lock_held(folder):
                raise RuntimeError(
                    "仍有实例持有运行锁，可能正在启动或繁忙；请等待，不会清除其状态。"
                )
            elif unconfirmed(folder):
                stale.append(folder)
        if len(live) > 1:
            raise RuntimeError("发现多个可认证实例，请逐一核对；本次没有更改运行选择。")
        archives = [recover_runtime(folder)["archived_state"] for folder in stale]
        if live:
            active, value = live[0]
            chosen = value["selection"]
            settings = {
                "profile": chosen["profile"],
                "ports": [service["port"] for service in value["services"]],
                "env_file": chosen["env_file"] if chosen["profile"] == "application" else "",
                "database": chosen["database"] if chosen["profile"] == "application" else "",
            }
        else:
            active = self.active if not self.state_error else None
            settings = self.preferences if not self.state_error else {}
        if self.state_error or (live and active != self.active):
            archives.append(archive_metadata(self.folder, [self.folder / "preferences.json"]))
            if active:
                write_json(
                    self.folder / "preferences.json",
                    {"preferences": settings, "active": str(active)},
                )
        with self.lock:
            self.active, self.preferences, self.state_error = active, settings, None
            self.preview = self.confirmation = None
            self.recovery = {
                "id": secrets.token_hex(16),
                "archives": [path for path in archives if path],
                "reattached": bool(live),
                "message": "已找回原运行选择，请核对当前状态。"
                if live
                else "旧控制状态已保留；请重新预览并确认数据后启动。"
                if any(archives)
                else "未发现需要恢复的旧控制状态；请核对配置后启动。",
            }

    def snapshot(self):
        from desktop.picker import picker_capability

        native_picker, picker_hint = picker_capability(self.folder)
        with self.lock:
            data = {
                "console_version": CONSOLE_VERSION,
                "mode": mode(),
                "project": str(ROOT),
                "state_dir": str(self.folder),
                "busy": self.busy,
                "exit_ready": self.exit_ready,
                "error": self.error,
                "preview": self.preview,
                "preferences": self.preferences,
                "picked": self.picked,
                "native_picker": native_picker,
                "picker_hint": picker_hint,
                "state_error": self.state_error,
                "recovery": self.recovery,
            }
            active = self.active
        data["runtime"] = current(active) if active else None
        data["runtime_unverified"] = bool(
            self.state_error or (active and not data["runtime"] and unconfirmed(active))
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
        if not self.active or not current(self.active):
            ensure_host_ports(self.folder, ports)
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
            self.confirmation = (
                {
                    key: payload[key]
                    for key in ("profile", "env_file", "database", "ports")
                    if key in payload
                },
                runtime_dir,
                fingerprint,
            )

    def launch(self, payload):
        if self.state_error:
            raise RuntimeError(self.state_error)
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
        running = None
        if self.active:
            running = current(self.active)
            if not running and unconfirmed(self.active):
                raise RuntimeError(
                    "上次启动或运行状态尚未确认，请先核对并恢复；不会改写原运行选择。"
                )
            if self.active != folder and running:
                raise ValueError("请先停止当前应用，再切换配置或数据。")
        if not running:
            ensure_host_ports(self.folder, preview["ports"])
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
        if self.state_error:
            raise RuntimeError(self.state_error)
        if self.active:
            if unconfirmed(self.active) and not current(self.active):
                raise RuntimeError(
                    "已有实例状态无法确认；保留现场，请核对运行日志，不会按旧PID终止进程。"
                )
            stop(self.active)

    def submit(self, action, payload):
        if action not in ("inspect", "prepare", "start", "stop", "exit", "pick", "recover"):
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
                elif action == "recover":
                    self.recover()
                elif action == "pick":
                    from desktop.picker import pick_file

                    request_id = payload.get("request_id")
                    if not isinstance(request_id, str) or not 16 <= len(request_id) <= 64:
                        raise ValueError("文件选择请求标识无效。")
                    path = pick_file(payload.get("kind"), self.folder)
                    with self.lock:
                        self.picked = {
                            "request_id": request_id,
                            "kind": payload["kind"],
                            "path": path,
                        }
                else:
                    self.stop_application()
                    if action == "exit":
                        self.exit_ready = True
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                with self.lock:
                    self.error = str(error)
            except Exception as error:
                with self.lock:
                    self.error = (
                        f"管理操作未完成（{type(error).__name__}）；已保留状态，请核对后重试。"
                    )
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
                    result = {
                        "instance": instance,
                        "pid": os.getpid(),
                        "console_version": CONSOLE_VERSION,
                    }
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


def open_browser(record):
    """Report browser handoff failures without exposing the private launch URL."""
    url = f"http://127.0.0.1:{record['port']}/#{record['token']}"
    try:
        opened = webbrowser.open(url)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        raise RuntimeError(
            "管理器已运行，但默认浏览器未打开；请配置本机浏览器后重新打开入口。"
        ) from None


def open_console(folder, *, browser=True):
    for child in _LAUNCHED[:]:
        if child.poll() is not None:
            child.wait()
            _LAUNCHED.remove(child)
    folder = prepare(folder)
    existing = live_record(folder)
    if existing and console_request(existing).get("console_version") != CONSOLE_VERSION:
        raise RuntimeError(
            "管理器来自旧版本；请在原管理页停止退出，"
            "或在该环境运行python -m desktop console-stop，然后重开入口。"
        )
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
        open_browser(existing)
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
