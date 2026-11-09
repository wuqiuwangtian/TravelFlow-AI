"""WSL chooser scopes, optional capability and name-only selection without file reads."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop.launcher import command
from desktop.picker import (
    configure_windows_picker,
    pick_file,
    picker_capability,
    selected_wsl_file,
    unc_path,
    windows_picker,
    wsl_selection_path,
)
from desktop.runtime import ROOT, prepare, write_json


class PickerScopeTests(unittest.TestCase):
    def test_only_current_distribution_unc_is_converted_without_drive_guessing(self):
        self.assertEqual(
            wsl_selection_path(r"\\wsl.localhost\Arch\home\中文 space ' $()\config.env", "Arch"),
            "/home/中文 space ' $()/config.env",
        )
        self.assertEqual(
            wsl_selection_path(r"\\?\UNC\wsl$\Arch\home\config.env", "Arch"), "/home/config.env"
        )
        self.assertEqual(wsl_selection_path("", "Arch"), "")
        for value in (
            r"C:\data\config.env",
            r"\\wsl$\Other\home\config.env",
            r"\\server\share\config.env",
            "/home/config.env",
            None,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wsl_selection_path(value, "Arch")

    def test_host_paths_travel_as_literal_arguments_and_no_shell_is_added(self):
        host = r"C:\Python 中文\pythonw.exe"
        args, cwd = command(
            {
                "mode": "wsl",
                "distribution": "Arch",
                "project": "/home/source ' $()",
                "python": "python3",
            },
            "a" * 32,
            picker_python=host,
        )
        self.assertIsNone(cwd)
        self.assertEqual(args[args.index("--picker-python") + 1], host)
        self.assertEqual(args[args.index("--picker-distribution") + 1], "Arch")
        self.assertNotIn("-c", args)
        self.assertEqual(
            unc_path("Arch", "/home/source ' $()"), r"\\wsl.localhost\Arch\home\source ' $()"
        )
        with self.assertRaises(ValueError):
            unc_path("Arch/Other", "/home/source")

    def test_capability_is_optional_and_bound_to_current_project_and_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare(Path(directory))
            executable = folder / "python.exe"
            executable.write_bytes(b"fixture executable")
            settings = {"project": str(ROOT), "distribution": "Arch", "executable": str(executable)}
            environment = SimpleNamespace(name="posix", environ={"WSL_DISTRO_NAME": "Arch"})
            with (
                patch("desktop.picker.mode", return_value="wsl"),
                patch("desktop.picker.os", environment),
            ):
                self.assertFalse(picker_capability(folder)[0])
                write_json(folder / "windows-picker.json", settings)
                self.assertTrue(picker_capability(folder)[0])
                for field, value in (
                    ("project", "other-source"),
                    ("distribution", "Other"),
                    ("executable", str(folder / "missing.exe")),
                ):
                    write_json(folder / "windows-picker.json", {**settings, field: value})
                    self.assertIsNone(windows_picker(folder))

    def test_native_dialog_deadline_exits_only_its_owned_helper_process(self):
        code = (
            "import time; from desktop import native_dialog; "
            "native_dialog.dialog=lambda *args: time.sleep(5); "
            "native_dialog.main(['env_file'],timeout=0.05)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, timeout=10
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(result.stdout)

    def test_missing_host_setup_keeps_manual_paths_available(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare(Path(directory))
            environment = SimpleNamespace(name="posix", environ={"WSL_DISTRO_NAME": "Arch"})
            with (
                patch("desktop.picker.mode", return_value="wsl"),
                patch("desktop.picker.os", environment),
                patch("desktop.picker.shutil.which", return_value=None),
            ):
                configure_windows_picker(folder, r"C:\Python\python.exe", "Arch")
                self.assertFalse(picker_capability(folder)[0])
                with self.assertRaises(RuntimeError):
                    pick_file("env_file", folder)
                self.assertFalse(list(folder.glob("*.sqlite")))
                with patch(
                    "desktop.picker.write_json",
                    side_effect=PermissionError("fixture metadata readonly"),
                ):
                    self.assertFalse(
                        configure_windows_picker(folder, r"C:\Python\python.exe", "Arch")
                    )


@unittest.skipIf(
    os.name == "nt", "WSL uses a POSIX filesystem; native dialogs are covered separately"
)
class WslSelectionTests(unittest.TestCase):
    def test_selected_file_is_validated_by_path_without_reading_its_contents(self):
        with tempfile.TemporaryDirectory(prefix="picker 中文 ' $() ") as directory:
            selected = Path(directory) / "fixture.env"
            selected.write_text("private fixture; must not be read", encoding="utf-8")
            with (
                patch.object(Path, "read_text", side_effect=AssertionError("file contents read")),
                patch.object(Path, "read_bytes", side_effect=AssertionError("file contents read")),
            ):
                self.assertEqual(
                    selected_wsl_file(unc_path("Arch", str(selected)), "Arch"),
                    str(selected.resolve()),
                )
                with self.assertRaises(ValueError):
                    selected_wsl_file(unc_path("Arch", str(selected.parent)), "Arch")
                with self.assertRaises(ValueError):
                    selected_wsl_file(
                        unc_path("Arch", str(selected.parent / "missing.env")), "Arch"
                    )

    def test_transport_cancellation_and_failures_preserve_selected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare(Path(directory))
            selected = folder / "fixture.env"
            selected.write_bytes(b"preserved fixture")
            settings = {"distribution": "Arch", "executable": "/fixture/python.exe"}
            environment = SimpleNamespace(name="posix", environ={})
            for value in ("", unc_path("Arch", str(selected))):
                with (
                    patch("desktop.picker.os", environment),
                    patch("desktop.picker.windows_picker", return_value=settings),
                    patch(
                        "desktop.picker.subprocess.run",
                        return_value=SimpleNamespace(
                            returncode=0, stdout=json.dumps({"path": value}).encode()
                        ),
                    ) as running,
                ):
                    result = pick_file("env_file", folder)
                    self.assertEqual(result, str(selected) if value else "")
                    args = running.call_args.args[0]
                    self.assertEqual(
                        args[1], unc_path("Arch", str(ROOT / "desktop/native_dialog.py"))
                    )
                    self.assertEqual(args[3], unc_path("Arch", str(ROOT)))
                    self.assertNotIn("shell", running.call_args.kwargs)
            with (
                patch("desktop.picker.os", environment),
                patch("desktop.picker.windows_picker", return_value=settings),
                patch(
                    "desktop.picker.subprocess.run",
                    side_effect=OSError("fixture interop unavailable"),
                ),
                self.assertRaises(RuntimeError),
            ):
                pick_file("env_file", folder)
            self.assertEqual(selected.read_bytes(), b"preserved fixture")


if __name__ == "__main__":
    unittest.main()
