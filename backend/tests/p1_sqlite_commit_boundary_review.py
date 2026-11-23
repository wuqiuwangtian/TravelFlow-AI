"""Measure SQLite process-exit boundaries with fictional rows, independently of the executor."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

FIXTURE = Path(__file__).with_name("fixtures") / "p1_expense_create_candidate.json"
INTERRUPTED = 86

# A small independent storage probe. Every insert happens before the first exit
# point. The worker never acknowledges success on stdout at either exit point.
WORKER = """
import json
import os
import sqlite3
import sys

database, fixture, phase = sys.argv[1:]
with open(fixture, encoding="utf-8") as stream:
    command = json.load(stream)["command"]
operation = command["operation"]["operation_id"]
db = sqlite3.connect(database)
db.execute("PRAGMA synchronous=FULL")
db.execute("BEGIN IMMEDIATE")
db.execute("INSERT INTO revisions VALUES (?, ?)", ("r1", command["expected_revision_id"]))
db.execute("INSERT INTO expenses VALUES (?, ?)", (
    command["target"]["entity_id"], command["payload"]["amount_cents"]))
db.execute("INSERT INTO receipts VALUES (?, ?, ?)", (operation, "r1", json.dumps(command)))
db.execute("INSERT INTO outbox VALUES (?)", (operation,))
if phase == "before_commit":
    os._exit(86)
db.commit()
os._exit(86)
"""


@pytest.mark.parametrize("phase", ["before_commit", "after_commit_before_reply"])
def test_process_exit_at_sqlite_commit_boundary(tmp_path, phase):
    database = tmp_path / "probe.sqlite"
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        db.executescript("""
            CREATE TABLE revisions(id PRIMARY KEY, parent);
            CREATE TABLE expenses(id PRIMARY KEY, amount_cents INTEGER);
            CREATE TABLE receipts(operation PRIMARY KEY, result_revision, intent);
            CREATE TABLE outbox(operation PRIMARY KEY);
            INSERT INTO revisions VALUES ('fixture-r0', NULL);
        """)
    result = subprocess.run(
        [sys.executable, "-c", WORKER, str(database), str(FIXTURE), phase],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == INTERRUPTED, result.stderr
    assert not result.stdout
    command = json.loads(FIXTURE.read_bytes())["command"]
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        revisions = db.execute("SELECT id, parent FROM revisions ORDER BY rowid").fetchall()
        expenses = db.execute("SELECT id, amount_cents FROM expenses").fetchall()
        receipts = db.execute("SELECT operation, result_revision, intent FROM receipts").fetchall()
        outbox = db.execute("SELECT operation FROM outbox").fetchall()
    if phase == "before_commit":
        assert revisions == [("fixture-r0", None)]
        assert expenses == receipts == outbox == []
    else:
        assert revisions == [("fixture-r0", None), ("r1", "fixture-r0")]
        assert expenses == [("fixture-lunch", 8635)]
        assert [(op, rev, json.loads(intent)) for op, rev, intent in receipts] == [
            ("fixture-op-lunch", "r1", command)
        ]
        assert outbox == [("fixture-op-lunch",)]
