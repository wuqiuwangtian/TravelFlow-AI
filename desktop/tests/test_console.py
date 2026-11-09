"""Management permissions, confirmation and actual HTTP identity monitoring."""

import json
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


if __name__ == "__main__":
    unittest.main()
