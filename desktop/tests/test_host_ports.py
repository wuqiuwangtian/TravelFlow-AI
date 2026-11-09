"""A healthy WSL listener must not bypass a Windows reserved-port failure."""

import json
import socket
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from desktop.console import Console
from desktop.host_ports import ensure_host_ports, probe_ports
from desktop.runtime import prepare


class HostPortTests(unittest.TestCase):
    def test_probe_releases_its_socket_and_reports_an_occupied_port(self):
        with socket.socket() as owner:
            owner.bind(("127.0.0.1", 0))
            owner.listen()
            port = owner.getsockname()[1]
            self.assertNotEqual(probe_ports([port])[0]["code"], 0)
        self.assertEqual(probe_ports([port]), [{"port": port, "code": 0}])
        with socket.socket() as replacement:
            replacement.bind(("127.0.0.1", port))

    def test_host_reserved_port_fails_with_literal_transport_and_no_settings_changes(self):
        host = {"executable": "/mnt/c/Program Files/python.exe", "distribution": "Fixture"}
        for code in (10013, 10048, 0):
            with (
                self.subTest(code=code),
                patch("desktop.runtime.ROOT", PurePosixPath("/fixture source 中文")),
                patch("desktop.picker.windows_picker", return_value=host),
                patch(
                    "desktop.host_ports.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout=json.dumps([{"port": 3100, "code": code}]).encode()
                    ),
                ) as running,
            ):
                if code:
                    with self.assertRaisesRegex(ValueError, f"3100.*{code}"):
                        ensure_host_ports(Path("unused"), [3100])
                else:
                    ensure_host_ports(Path("unused"), [3100])
                args, kwargs = running.call_args
                self.assertEqual(args[0][0], host["executable"])
                self.assertTrue(args[0][1].startswith(r"\\wsl.localhost\Fixture"))
                self.assertEqual(args[0][-1], "3100")
                self.assertNotIn("shell", kwargs)

    def test_optional_host_and_invalid_or_failed_response_never_claim_success(self):
        with (
            patch("desktop.picker.windows_picker", return_value=None),
            patch("desktop.host_ports.subprocess.run") as running,
        ):
            ensure_host_ports(Path("unused"), [3100])
            running.assert_not_called()
        for output in (b"private-host-value", b"[]", b'[{"port":8000,"code":0}]'):
            with (
                patch("desktop.runtime.ROOT", PurePosixPath("/fixture source 中文")),
                patch(
                    "desktop.picker.windows_picker",
                    return_value={"executable": "host", "distribution": "Fixture"},
                ),
                patch(
                    "desktop.host_ports.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=output),
                ) as running,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    ensure_host_ports(Path("unused"), [3100])
                self.assertNotIn("private-host-value", str(raised.exception))
                running.assert_called_once()

    def test_preview_and_launch_recheck_before_persisting_or_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare(Path(directory))
            state = Console(folder)
            payload = {"profile": "application-qa", "ports": [8000, 3100]}
            with (
                patch("desktop.console.ensure_host_ports", side_effect=ValueError("reserved")),
                self.assertRaisesRegex(ValueError, "reserved"),
            ):
                state.inspect(payload)
            self.assertIsNone(state.preview)
            with patch("desktop.console.ensure_host_ports"):
                state.inspect(payload)
            with (
                patch("desktop.console.ensure_host_ports", side_effect=ValueError("reserved")),
                patch("desktop.console.start") as starting,
            ):
                with self.assertRaisesRegex(ValueError, "reserved"):
                    state.launch({"confirmation": state.preview["confirmation"]})
                starting.assert_not_called()
            self.assertFalse((folder / "preferences.json").exists())
            self.assertFalse((folder / "qa/qa.sqlite").exists())

    def test_live_managed_instance_is_not_rejected_by_its_own_windows_forwarder(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = prepare(Path(directory))
            state = Console(folder)
            state.active = folder / "qa"
            with (
                patch("desktop.console.current", return_value={"ready": True}),
                patch(
                    "desktop.console.ensure_host_ports",
                    side_effect=AssertionError("must not bind live ports"),
                ),
                patch("desktop.console.start") as starting,
            ):
                state.inspect({"profile": "application-qa", "ports": [8000, 3100]})
                state.launch({"confirmation": state.preview["confirmation"]})
                starting.assert_called_once()
