"""Opt-in Windows/WSL backup handoff, using one disposable directory on each filesystem."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from desktop.backup import checksum, create_backup, restore_backup
from desktop.runtime import ROOT, database_lock, mode, selection
from desktop.tests.test_backup import unpack_sample

WINDOWS_PYTHON = os.environ.get("TRAVELFLOW_WINDOWS_TEST_PYTHON")
ORIGINAL = "北京三日游"
EDITED = "Windows 副本的虚构旅行"


def qa_selection(path):
    return {**selection(path), "profile": "application-qa"}


def title(path, *, edit=False):
    with (
        database_lock(qa_selection(path)),
        closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as db,
    ):
        if edit:
            db.execute("UPDATE sessions SET title = ?", (EDITED,))
            db.commit()
        return db.execute("SELECT title FROM sessions").fetchall()


def windows_round_trip(folder, wsl_source):
    if mode() != "windows-native":
        raise RuntimeError("Windows native Python required")
    result = restore_backup(folder / "from-wsl", folder / "copy")
    database = Path(result["path"])
    checks = unittest.TestCase()
    with (
        checks.assertRaisesRegex(RuntimeError, "归属其他"),
        database_lock(qa_selection(wsl_source)),
    ):
        checks.fail("Windows accepted the WSL-owned database")
    checks.assertEqual(checksum(database), result["sha256"])
    checks.assertEqual(title(database), [(ORIGINAL,)])
    checks.assertEqual(title(database, edit=True), [(EDITED,)])
    create_backup(database, folder / "from-windows")
    checks.assertEqual(title(database), [(EDITED,)])


def converted(flag, path):
    return subprocess.check_output(["wslpath", flag, str(path)], text=True).strip()


@unittest.skipUnless(mode() == "wsl" and WINDOWS_PYTHON, "Requires WSL and a Windows Python path")
class CrossModeTests(unittest.TestCase):
    def test_return_with_windows_edits_and_preserve_both_sources(self):
        # Use native temporary storage for each active database. Transfer closed packages only.
        win_temp = json.loads(
            subprocess.check_output(
                [
                    WINDOWS_PYTHON,
                    "-c",
                    "import tempfile,json; print(json.dumps(tempfile.gettempdir()))",
                ],
                timeout=20,
            )
        )
        with (
            tempfile.TemporaryDirectory(prefix="D1-WSL-") as linux_dir,
            tempfile.TemporaryDirectory(
                dir=converted("-u", win_temp), prefix="D1-Windows-"
            ) as win_dir,
        ):
            linux, windows = Path(linux_dir), Path(win_dir)
            source = linux / "source.sqlite"
            unpack_sample(source)
            self.assertEqual(title(source), [(ORIGINAL,)])
            original = checksum(source)
            create_backup(source, linux / "out")
            shutil.copytree(linux / "out", windows / "from-wsl")
            subprocess.run(
                [
                    WINDOWS_PYTHON,
                    "-m",
                    "desktop.tests.test_cross_mode",
                    converted("-w", windows),
                    converted("-w", source),
                ],
                cwd=ROOT,
                check=True,
                timeout=60,
            )
            foreign = windows / "copy/travel.sqlite"
            foreign_digest = checksum(foreign)
            with (
                self.assertRaisesRegex(RuntimeError, "归属其他"),
                database_lock(qa_selection(foreign)),
            ):
                self.fail("WSL accepted the Windows-owned database")
            shutil.copytree(windows / "from-windows", linux / "in")
            returned = restore_backup(linux / "in", linux / "returned")
            copy = Path(returned["path"])
            self.assertEqual(checksum(copy), returned["sha256"])
            self.assertEqual(title(copy), [(EDITED,)])
            self.assertEqual(title(source), [(ORIGINAL,)])
            self.assertEqual(checksum(source), original)
            self.assertEqual(checksum(foreign), foreign_digest)


if __name__ == "__main__":
    windows_round_trip(Path(sys.argv[1]), Path(sys.argv[2]))
