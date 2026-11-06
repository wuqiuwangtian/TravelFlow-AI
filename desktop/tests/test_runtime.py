"""Real local processes and sockets, synthetic data only; Windows and POSIX."""

import ctypes
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desktop.runtime import (
    ROOT,
    current,
    prepare,
    record,
    request,
    selection,
    start,
    stop,
)


def ports():
    sockets = [socket.socket(), socket.socket()]
    try:
        for item in sockets:
            item.bind(("127.0.0.1", 0))
        return [item.getsockname()[1] for item in sockets]
    finally:
        for item in sockets:
            item.close()


def alive(pid):
    if os.name == "nt":
        from ctypes import wintypes as w

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = w.DWORD()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        proc = Path(f"/proc/{pid}/stat")
        if proc.exists() and proc.read_text().split(")", 1)[1].strip().startswith("Z"):
            return False
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, FileNotFoundError):
        return False


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="travelflow D0 中文 ")
        self.folder = prepare(Path(self.temporary.name) / "runtime")
        self.chosen = selection(self.folder / "virtual-trip.sqlite")
        self.ports = ports()

    def tearDown(self):
        try:
            stop(self.folder)
        finally:
            # A broken stop implementation must fail the test without leaking test services.
            running = current(self.folder)
            if running:
                meta = record(self.folder)
                request(meta["port"], "/stop", meta["token"], "POST")
                self.assert_gone([running["manager_pid"]])
            self.temporary.cleanup()

    def launch(self):
        try:
            return start(self.folder, self.chosen, self.ports)
        except RuntimeError:
            log = self.folder / "manager.log"
            self.fail(log.read_text(errors="replace") if log.exists() else "No manager log")

    def assert_gone(self, pids):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and any(alive(pid) for pid in pids):
            time.sleep(0.05)
        self.assertFalse([pid for pid in pids if alive(pid)])

    def test_start_reuse_stop_cleans_real_descendants(self):
        result = self.launch()
        self.assertTrue(result["ready"])
        self.assertEqual(result["selection"], self.chosen)
        repeated = self.launch()
        self.assertEqual(result["instance"], repeated["instance"])
        self.assertTrue(repeated["reused"])
        pids = [item[key] for item in result["services"] for key in ("pid", "descendant_pid")]
        self.assertTrue(all(alive(pid) for pid in pids))
        self.assertNotIn(record(self.folder)["token"], json.dumps(result))
        self.assertFalse(Path(self.chosen["database"]).exists())
        self.assertTrue(stop(self.folder)["stopped"])
        self.assert_gone([result["manager_pid"], *pids])
        self.assertIsNone(current(self.folder))
        self.assertTrue(stop(self.folder)["stopped"])

    def test_wrong_capability_and_foreign_origin_cannot_stop_instance(self):
        result = self.launch()
        meta = record(self.folder)
        for invalid in ("wrong-token", "é" * 64):
            with self.assertRaises(urllib.error.HTTPError) as error:
                request(meta["port"], "/stop", invalid, "POST")
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

        req = urllib.request.Request(
            f"http://127.0.0.1:{meta['port']}/stop",
            data=b"",
            headers={
                "X-TravelFlow-Control": meta["token"],
                "Origin": "https://unrelated.example",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(req, timeout=2)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        self.assertEqual(current(self.folder)["instance"], result["instance"])

    def test_foreign_service_is_refused_and_survives(self):
        class Foreign(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"instance":"foreign"}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Foreign)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            occupied = [server.server_port, self.ports[1]]
            with self.assertRaises(RuntimeError):
                start(self.folder, self.chosen, occupied)
            self.assertEqual(request(server.server_port, "/health")["instance"], "foreign")
            self.assertIsNone(record(self.folder))
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_changing_database_is_refused_even_after_stop(self):
        self.launch()
        stop(self.folder)
        with self.assertRaises(RuntimeError):
            start(self.folder, selection(self.folder / "other.sqlite"), self.ports)
        self.assertFalse((self.folder / "other.sqlite").exists())

    def test_second_service_failure_cleans_first_service_tree(self):
        from unittest.mock import patch

        from desktop.processes import OwnedProcess
        from desktop.runtime import run_supervisor

        captured = []
        calls = 0

        def launch(command, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                value = request(self.ports[0], "/health")
                captured.extend([value["pid"], value["descendant_pid"]])
                command = [sys.executable, "-c", "raise SystemExit(1)"]
            return OwnedProcess(command, **kwargs)

        with (
            patch("desktop.runtime.OwnedProcess", side_effect=launch),
            self.assertRaises(RuntimeError),
        ):
            run_supervisor(self.folder, self.chosen, self.ports)
        self.assertEqual(len(captured), 2)
        self.assert_gone(captured)
        self.assertIsNone(record(self.folder))

    def test_outside_database_and_duplicate_ports_are_rejected_before_spawn(self):
        with self.assertRaises(ValueError):
            start(
                self.folder,
                selection(Path(self.temporary.name) / "outside.sqlite"),
                self.ports,
            )
        with self.assertRaises(ValueError):
            start(self.folder, self.chosen, [self.ports[0]] * 2)
        self.assertIsNone(record(self.folder))

    def test_stale_state_cannot_trigger_pid_kills_or_claim_success(self):
        from desktop.runtime import write_json

        write_json(
            self.folder / "runtime.json",
            {
                "port": self.ports[0],
                "token": "a" * 64,
                "instance": "stale",
                "pid": os.getpid(),
            },
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "Owner is not verified"):
                stop(self.folder)
            self.assertTrue(alive(os.getpid()))
            self.assertTrue((self.folder / "runtime.json").exists())
        finally:
            (self.folder / "runtime.json").unlink()

    def test_manager_crash_cleans_descendants_without_pid_state_signals(self):
        command = [
            sys.executable,
            "-m",
            "desktop",
            "_serve",
            "--probe",
            "--state-dir",
            str(self.folder),
            "--database",
            self.chosen["database"],
            "--ports",
            *map(str, self.ports),
        ]
        with open(self.folder / "crash-test.log", "wb") as log:
            manager = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                result = None
                while time.monotonic() < deadline:
                    result = current(self.folder)
                    if result:
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(result)
                pids = [
                    item[key] for item in result["services"] for key in ("pid", "descendant_pid")
                ]
                manager.kill()  # Owned Popen handle, not a persisted PID.
                manager.wait(timeout=5)
                self.assert_gone(pids)
            finally:
                if manager.poll() is None:
                    manager.kill()
                    manager.wait(timeout=5)
                (self.folder / "runtime.json").unlink(missing_ok=True)

    def test_simultaneous_cli_starts_share_one_instance(self):
        command = [
            sys.executable,
            "-m",
            "desktop",
            "start",
            "--probe",
            "--state-dir",
            str(self.folder),
            "--database",
            self.chosen["database"],
            "--ports",
            *map(str, self.ports),
        ]
        children = [
            subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(2)
        ]
        results = []
        for child in children:
            out, err = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, err.decode(errors="replace"))
            results.append(json.loads(out))
        self.assertEqual(results[0]["instance"], results[1]["instance"])
        self.assertTrue(stop(self.folder)["stopped"])
        self.assert_gone([results[0]["manager_pid"]])


if __name__ == "__main__":
    unittest.main()
