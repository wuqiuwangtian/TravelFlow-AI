"""Source selection, private bridge validation and optional real Windows Tk controls."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop.bridge import connect_payload
from desktop.console import Console, close_console, console_request, open_console
from desktop.launcher import command, connect, distributions, project_path
from desktop.runtime import ROOT, mode, prepare


class LauncherTests(unittest.TestCase):
    def test_windows_distribution_encodings_and_explicit_source_boundaries(self):
        self.assertEqual(
            distributions("Arch\r\nUbuntu 中文\r\n".encode("utf-16")), ["Arch", "Ubuntu 中文"]
        )
        self.assertEqual(distributions(b"Arch\n"), ["Arch"])
        self.assertEqual(
            project_path("wsl", r"\\wsl.localhost\Arch\home\测试 项目", "Arch"), "/home/测试 项目"
        )
        self.assertEqual(
            project_path("windows-native", "C:/code/Travel Flow"), r"C:\code\Travel Flow"
        )
        for selected_mode, path, distro in (
            ("wsl", r"\\wsl$\Ubuntu\home\project", "Arch"),
            ("windows-native", r"\\wsl$\Arch\home\project", ""),
            ("wsl", "C:/code/project", "Arch"),
            ("", "C:/code/project", ""),
        ):
            with self.assertRaises(ValueError):
                project_path(selected_mode, path, distro)

    def test_wsl_command_preserves_literal_arguments_and_never_uses_a_shell(self):
        project = "/home/test/中文 space ' $(touch never)"
        args, cwd = command(
            {"mode": "wsl", "distribution": "Arch", "project": project, "python": "python3"},
            "a" * 32,
        )
        self.assertIsNone(cwd)
        self.assertEqual(args[args.index("--cd") + 1], project)
        self.assertIn("--exec", args)
        self.assertNotIn("bash", args)
        self.assertNotIn("-c", args)
        with self.assertRaises(ValueError):
            command(
                {"mode": "wsl", "distribution": "", "project": project, "python": "python3"},
                "a" * 32,
            )

    def test_bridge_bad_replies_never_open_browser_or_contact_arbitrary_ports(self):
        profile = {
            "mode": "wsl",
            "distribution": "Arch",
            "project": "/home/project",
            "python": "python3",
        }
        nonce = "a" * 32
        base = {"bridge_version": 1, "nonce": nonce, "mode": "wsl", "project": "/home/project"}
        for value in (
            [],
            {**base, "nonce": "wrong"},
            {**base, "mode": "windows-native"},
            {**base, "project": None},
            {**base, "record": []},
            {**base, "record": {"port": 80, "token": 1, "instance": "x"}},
        ):
            with (
                self.subTest(value=value),
                patch("desktop.launcher.secrets.token_hex", return_value=nonce),
                patch(
                    "desktop.launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=json.dumps(value).encode()),
                ),
                patch("desktop.launcher.webbrowser.open") as browser,
                patch("desktop.console.console_request") as request,
            ):
                with self.assertRaises(RuntimeError):
                    connect(profile)
                browser.assert_not_called()
                request.assert_not_called()

    def test_bridge_reports_old_interpreter_without_loading_console(self):
        with (
            patch("desktop.bridge.sys.version_info", (3, 12)),
            patch("desktop.console.open_console") as opening,
        ):
            with self.assertRaisesRegex(RuntimeError, "3.13"):
                connect_payload(Path("unused"), "a" * 32)
            opening.assert_not_called()

    def test_failed_target_does_not_fall_back_to_another_mode(self):
        nonce = "a" * 32
        profile = {
            "mode": "wsl",
            "distribution": "Arch",
            "project": "/home/project",
            "python": "python3",
        }
        error = {"bridge_version": 1, "nonce": nonce, "error": "fixture: select newer Python"}
        with (
            patch("desktop.launcher.secrets.token_hex", return_value=nonce),
            patch(
                "desktop.launcher.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stdout=json.dumps(error).encode()),
            ) as running,
        ):
            with self.assertRaisesRegex(RuntimeError, "newer Python"):
                connect(profile)
            self.assertEqual(running.call_count, 1)

    def test_old_console_is_not_silently_reused_or_killed(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("desktop.console.live_record", return_value={"port": 1}),
            patch("desktop.console.console_request", return_value={"console_version": 1}),
            patch("desktop.console.subprocess.Popen") as spawning,
        ):
            with self.assertRaisesRegex(RuntimeError, "旧版本"):
                open_console(Path(directory), browser=False)
            spawning.assert_not_called()

    def test_private_cli_bridge_opens_only_management_and_returns_bound_identity(self):
        with tempfile.TemporaryDirectory(prefix="travelflow bridge 中文 ") as directory:
            folder = Path(directory)
            try:
                nonce = "b" * 32
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "desktop",
                        "_connect",
                        "--nonce",
                        nonce,
                        "--state-dir",
                        str(folder),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    check=True,
                )
                reply = json.loads(completed.stdout)
                self.assertEqual(reply["nonce"], nonce)
                self.assertEqual(reply["mode"], mode())
                self.assertEqual(reply["project"], str(ROOT))
                self.assertEqual(
                    console_request(reply["record"])["instance"], reply["record"]["instance"]
                )
                self.assertIsNone(console_request(reply["record"], "/api/state")["runtime"])
                self.assertFalse(list(folder.rglob("*.sqlite")))
                stopping = subprocess.run(
                    [sys.executable, "-m", "desktop", "console-stop", "--state-dir", str(folder)],
                    cwd=ROOT,
                    capture_output=True,
                    check=True,
                )
                self.assertEqual(
                    json.loads(stopping.stdout), {"stopped": True, "was_running": True}
                )
                self.assertFalse((folder / "console.json").exists())
            finally:
                close_console(folder)

    def test_file_selection_cancel_preserves_form_and_does_not_open_database(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Console(prepare(Path(directory)))
            request = {"kind": "database", "request_id": "a" * 32}
            with patch("desktop.picker.pick_file", return_value=""):
                state.submit("pick", request)
                deadline = time.monotonic() + 3
                while state.snapshot()["busy"] and time.monotonic() < deadline:
                    time.sleep(0.01)
            self.assertIsNone(state.snapshot()["error"])
            self.assertEqual(state.snapshot()["picked"]["path"], "")
            self.assertEqual(state.snapshot()["preferences"], {})
            self.assertEqual(list(Path(directory).glob("*.sqlite")), [])


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("TRAVELFLOW_RUN_NATIVE_UI_TESTS") == "1",
    "Requires explicit Windows Tk acceptance",
)
class NativeWindowTests(unittest.TestCase):
    def test_native_dialog_returns_filename_without_opening_contents(self):
        from desktop.picker import dialog

        selected = r"C:\isolated fixture\config.env"
        with patch("tkinter.filedialog.askopenfilename", return_value=selected) as choosing:
            self.assertEqual(dialog("env_file"), selected)
        self.assertIn("配置", choosing.call_args.kwargs["title"])
        with patch("tkinter.filedialog.askopenfilename", return_value=""):
            self.assertEqual(dialog("database"), "")

    def test_real_controls_require_mode_and_report_worker_failure(self):
        import tkinter as tk

        from desktop.launcher import build_launcher

        root = tk.Tk()
        root.withdraw()
        entered, release = threading.Event(), threading.Event()

        def fail(_):
            entered.set()
            release.wait(5)
            raise RuntimeError("fixture target unavailable")

        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch("desktop.launcher.connect", side_effect=fail) as connecting,
            ):
                ui = build_launcher(root, Path(directory) / "launcher.json")
                self.assertEqual(ui["variables"]["mode"].get(), "")
                ui["launch_button"].invoke()
                self.assertFalse(entered.is_set())
                ui["variables"]["mode"].set("Windows 原生")
                ui["widgets"][0].event_generate("<<ComboboxSelected>>")
                ui["variables"]["project"].set(directory)
                ui["launch_button"].invoke()
                self.assertTrue(entered.wait(3))
                self.assertTrue(ui["launch_button"].instate(["disabled"]))
                release.set()
                deadline = time.monotonic() + 5
                while (
                    "fixture target unavailable" not in ui["status"].get()
                    and time.monotonic() < deadline
                ):
                    root.update()
                    time.sleep(0.02)
                self.assertIn("fixture target unavailable", ui["status"].get())
                self.assertFalse(ui["launch_button"].instate(["disabled"]))
                self.assertEqual(connecting.call_count, 1)
                self.assertFalse((Path(directory) / "launcher.json").exists())
        finally:
            release.set()
            root.destroy()


@unittest.skipUnless(
    os.name == "nt" and sys.version_info >= (3, 13), "Native bridge requires Windows Python 3.13+"
)
class NativeBridgeTests(unittest.TestCase):
    def test_native_source_copy_connects_without_shell_or_business_start(self):
        from desktop.processes import supervisor_python

        with tempfile.TemporaryDirectory(prefix="travelflow source 中文 space ' ") as directory:
            root = Path(directory).resolve()
            shutil.copytree(
                ROOT / "desktop",
                root / "desktop",
                ignore=shutil.ignore_patterns("__pycache__", "tests"),
            )
            folder = root / "isolated-state"
            profile = {
                "mode": "windows-native",
                "project": str(root),
                "python": supervisor_python(),
            }
            try:
                record = connect(profile, browser=False, state_dir=str(folder))
                self.assertEqual(console_request(record, "/api/state")["project"], str(root))
                self.assertIsNone(console_request(record, "/api/state")["runtime"])
            finally:
                close_console(folder)


if __name__ == "__main__":
    unittest.main()
