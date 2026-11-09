"""Recovery touches owned control metadata, never databases or persisted PIDs."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.console import Console
from desktop.runtime import (
    ROOT,
    identity,
    instance_lock,
    launch_ticket_path,
    prepare,
    recover,
    selection,
    stop,
    write_json,
)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="travelflow recovery 中文 ")
        self.folder = prepare(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def preferences(self, active):
        path = self.folder / "preferences.json"
        write_json(
            path,
            {
                "preferences": {"profile": "application-qa", "ports": [18081, 18082]},
                "active": str(active),
            },
        )
        return path

    def test_held_startup_lock_prevents_false_stop_switch_and_recovery(self):
        active = prepare(self.folder / "qa")
        path = self.preferences(active)
        original = path.read_bytes()
        console = Console(self.folder)
        with instance_lock(active):
            self.assertTrue(console.snapshot()["runtime_unverified"])
            console.inspect({"profile": "application-qa", "ports": [18091, 18092]})
            with self.assertRaises(RuntimeError):
                console.launch({"confirmation": console.preview["confirmation"]})
            with self.assertRaises(RuntimeError):
                stop(active)
            with self.assertRaises(RuntimeError):
                console.stop_application()
            with self.assertRaises(RuntimeError):
                console.recover()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(console.active, active)
        self.assertFalse(list(self.folder.rglob("*.sqlite")))

    def test_corrupt_preferences_and_stale_control_are_preserved_without_opening_data(self):
        active = prepare(self.folder / "qa")
        bad_preferences = b"broken private fixture preferences"
        (self.folder / "preferences.json").write_bytes(bad_preferences)
        (active / "runtime.json").write_bytes(b"broken control fixture")
        descriptor = active / "selection.json"
        descriptor.write_bytes(b"preserved data selection")
        database = active / "qa.sqlite"
        database.write_bytes(b"fixture bytes must not be touched")
        console = Console(self.folder)
        self.assertTrue(console.snapshot()["state_error"])
        self.assertNotIn("private fixture", json.dumps(console.snapshot()))
        with self.assertRaises(RuntimeError):
            console.launch({})
        console.recover()
        self.assertIsNone(console.snapshot()["state_error"])
        self.assertIsNone(console.active)
        self.assertEqual(console.preferences, {})
        self.assertFalse((active / "runtime.json").exists())
        self.assertEqual(descriptor.read_bytes(), b"preserved data selection")
        self.assertEqual(database.read_bytes(), b"fixture bytes must not be touched")
        backups = list(self.folder.rglob("recovery/*/preferences.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), bad_preferences)
        self.assertIsNone(Console(self.folder).state_error)

    def test_recovery_invalidates_a_delayed_real_start_before_any_service_launch(self):
        active = prepare(self.folder / "qa")
        nonce = "a" * 32
        chosen = selection(active / "descriptor.sqlite")
        ticket = launch_ticket_path(active, nonce)
        write_json(ticket, {"selection": identity(chosen), "ports": [18081, 18082]})
        code = (
            "import sys; from pathlib import Path; "
            "from desktop.runtime import run_supervisor,selection; "
            "sys.stdin.buffer.readline(); p=Path(sys.argv[1]); "
            "run_supervisor(p,selection(p/'descriptor.sqlite'),[18081,18082],launch_ticket=sys.argv[2])"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(active), nonce],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            archive = recover(active)["archived_state"]
            self.assertTrue((Path(archive) / ticket.name).exists())
            out, error = process.communicate(b"continue\n", timeout=10)
            self.assertNotEqual(process.returncode, 0, out.decode(errors="replace"))
            self.assertIn("cancelled", error.decode(errors="replace"))
            self.assertFalse((active / "services.log").exists())
            self.assertFalse((active / "descriptor.sqlite").exists())
        finally:
            if process.poll() is None:
                process.communicate(b"continue\n", timeout=10)

    def test_pending_start_without_preferences_blocks_silent_new_selection(self):
        active = prepare(self.folder / "qa")
        write_json(launch_ticket_path(active, "b" * 32), {"fixture": "pending"})
        console = Console(self.folder)
        self.assertTrue(console.snapshot()["runtime_unverified"])
        with self.assertRaises(RuntimeError):
            console.launch({})
        console.recover()
        self.assertIsNone(console.state_error)
        self.assertFalse(list(active.glob("pending-*.json")))
        self.assertIsNone(console.preview)
        self.assertFalse(list(active.glob("*.sqlite")))

    def test_a_valid_stopped_selection_cannot_hide_another_unconfirmed_instance(self):
        selected = prepare(self.folder / "qa")
        self.preferences(selected)
        other = prepare(self.folder / "instances" / ("c" * 64))
        (other / "runtime.json").write_text("broken fixture record", encoding="utf-8")
        console = Console(self.folder)
        self.assertTrue(console.state_error)
        with self.assertRaises(RuntimeError):
            console.launch({})
        console.recover()
        self.assertIsNone(console.state_error)
        self.assertFalse((other / "runtime.json").exists())
        self.assertFalse(list(self.folder.rglob("*.sqlite")))

    def test_linked_lock_is_not_opened_or_modified_by_recovery(self):
        active = prepare(self.folder / "qa")
        target = self.folder / "untouched-fixture.txt"
        target.write_bytes(b"original fixture")
        try:
            (active / "manager.lock").symlink_to(target)
        except OSError:
            self.skipTest("This platform does not permit symlink creation")
        console = Console(self.folder)
        self.assertTrue(console.state_error)
        with self.assertRaises(RuntimeError):
            console.recover()
        self.assertEqual(target.read_bytes(), b"original fixture")
        self.assertTrue((active / "manager.lock").is_symlink())

    def test_unexpected_worker_error_is_visible_without_private_exception_values(self):
        console = Console(self.folder)
        with patch.object(console, "inspect", side_effect=KeyError("private exception fixture")):
            console.submit("inspect", {})
            deadline = time.monotonic() + 3
            while console.snapshot()["busy"] and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertIsNone(console.busy)
        self.assertIn("KeyError", console.error)
        self.assertNotIn("private exception fixture", console.error)


if __name__ == "__main__":
    unittest.main()
