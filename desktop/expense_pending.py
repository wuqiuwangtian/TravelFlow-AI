"""Isolated sender-side persistent pending-intent store and recovery workflow for Phase 2."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from desktop.expense_executor import (
    CANONICAL_PROFILE,
    QUERY_PROFILE,
    CallerContext,
    Rejected,
    canonicalize_intent,
    execute_command,
    lookup_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SENDER_STORE_FORMAT = "travelflow-p2-pending-sender/0-draft"
SENDER_APPLICATION_ID = 0x54465053
SENDER_SCHEMA_VERSION = 1

SENDER_REQUIRED_TABLES = frozenset({"sender_store_meta", "pending_operations"})

SENDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS sender_store_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    store_format TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_operations (
    dataset_id TEXT NOT NULL,
    source_device_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    expected_revision_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    canonical_profile TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    canonical_intent_json TEXT NOT NULL,
    raw_input_json TEXT NOT NULL,
    delivery_state TEXT NOT NULL CHECK (
        delivery_state IN ('unsent', 'verification_pending', 'confirmed')
    ),
    last_error TEXT,
    last_raw_response_json TEXT,
    confirmed_revision_id TEXT,
    confirmed_effect TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, source_device_id, operation_id)
);
"""


def init_sender_store(db_path: str | Path) -> None:
    """Initialize a brand-new isolated sender SQLite store. Refuses pre-existing paths."""
    path = Path(db_path)
    if os.path.lexists(path):
        raise ValueError("Refusing to initialize existing path; choose a new sender database.")
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.parent.resolve(strict=True) / path.name
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        raise ValueError(
            "Refusing to initialize existing path; choose a new sender database."
        ) from None

    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(f"{target}{suffix}"):
            target.unlink()
            raise ValueError("Existing SQLite companion file; choose a new sender database.")

    now = datetime.now(UTC).isoformat()
    try:
        with closing(sqlite3.connect(str(target), timeout=10.0, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(f"PRAGMA application_id = {SENDER_APPLICATION_ID}")
            db.execute(f"PRAGMA user_version = {SENDER_SCHEMA_VERSION}")
            db.executescript(SENDER_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO sender_store_meta(id, store_format, created_at) VALUES (1, ?, ?)",
                (SENDER_STORE_FORMAT, now),
            )
            db.execute("COMMIT")
    except BaseException:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        raise


def _validate_sender_store(db_path: str | Path) -> Path:
    path = Path(db_path)
    if path.is_symlink() or not path.is_file():
        raise Rejected("invalid_sender_store")
    resolved = path.resolve(strict=True)
    try:
        with closing(sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=10.0)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            user_ver = db.execute("PRAGMA user_version").fetchone()[0]
            if app_id != SENDER_APPLICATION_ID or user_ver != SENDER_SCHEMA_VERSION:
                raise Rejected("invalid_sender_store")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            }
            if not tables >= SENDER_REQUIRED_TABLES:
                raise Rejected("invalid_sender_store")
            meta = db.execute("SELECT store_format FROM sender_store_meta WHERE id = 1").fetchone()
            if meta is None or meta["store_format"] != SENDER_STORE_FORMAT:
                raise Rejected("invalid_sender_store")
    except sqlite3.Error:
        raise Rejected("invalid_sender_store") from None
    return resolved


def _connect_sender(resolved: Path, *, read_only: bool) -> sqlite3.Connection:
    mode = "ro" if read_only else "rw"
    conn = sqlite3.connect(
        f"{resolved.as_uri()}?mode={mode}", uri=True, timeout=10.0, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute("PRAGMA busy_timeout=10000")
    if not read_only:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_pending_dict(row: sqlite3.Row) -> dict:
    return {
        "operation": {
            "dataset_id": row["dataset_id"],
            "source_device_id": row["source_device_id"],
            "operation_id": row["operation_id"],
        },
        "kind": row["kind"],
        "expected_revision_id": row["expected_revision_id"],
        "target": {
            "travel_id": row["travel_id"],
            "branch_id": row["branch_id"],
            "entity_id": row["entity_id"],
        },
        "canonical_profile": row["canonical_profile"],
        "intent_sha256": row["intent_sha256"],
        "canonical_intent_json": row["canonical_intent_json"],
        "raw_input_json": row["raw_input_json"],
        "delivery_state": row["delivery_state"],
        "last_error": row["last_error"],
        "last_raw_response": (
            json.loads(row["last_raw_response_json"])
            if row["last_raw_response_json"] is not None
            else None
        ),
        "confirmed_revision_id": row["confirmed_revision_id"],
        "confirmed_effect": row["confirmed_effect"],
    }


def stage_pending_operation(sender_db: str | Path, raw_command: bytes) -> dict:
    """Persist full original operation key, baseline, and intent before any peer delivery."""
    try:
        resolved = _validate_sender_store(sender_db)
        command, canonical_bytes, intent_sha256 = canonicalize_intent(raw_command, "control")
    except Rejected as error:
        return {"ok": False, "error": error.code}

    op = command["operation"]
    target = command["target"]
    canonical_text = canonical_bytes.decode("utf-8")
    raw_text = raw_command.decode("utf-8")
    now = datetime.now(UTC).isoformat()

    with closing(_connect_sender(resolved, read_only=False)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            existing = db.execute(
                "SELECT * FROM pending_operations "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (op["dataset_id"], op["source_device_id"], op["operation_id"]),
            ).fetchone()
            if existing is not None:
                db.execute("ROLLBACK")
                if (
                    existing["intent_sha256"] == intent_sha256
                    and existing["canonical_intent_json"] == canonical_text
                ):
                    return {"ok": True, "staged": False, "record": _row_to_pending_dict(existing)}
                return {"ok": False, "error": "intent_conflict"}

            db.execute(
                "INSERT INTO pending_operations("
                "dataset_id, source_device_id, operation_id, kind, expected_revision_id, "
                "travel_id, branch_id, entity_id, canonical_profile, intent_sha256, "
                "canonical_intent_json, raw_input_json, delivery_state, last_error, "
                "last_raw_response_json, confirmed_revision_id, confirmed_effect, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unsent', NULL, NULL, NULL, NULL, "
                "?, ?)",
                (
                    op["dataset_id"],
                    op["source_device_id"],
                    op["operation_id"],
                    command["kind"],
                    command["expected_revision_id"],
                    target["travel_id"],
                    target["branch_id"],
                    target["entity_id"],
                    CANONICAL_PROFILE,
                    intent_sha256,
                    canonical_text,
                    raw_text,
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM pending_operations "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (op["dataset_id"], op["source_device_id"], op["operation_id"]),
            ).fetchone()
            db.execute("COMMIT")
            return {"ok": True, "staged": True, "record": _row_to_pending_dict(row)}
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise


def read_pending_operation(
    sender_db: str | Path,
    *,
    dataset_id: str,
    source_device_id: str,
    operation_id: str,
) -> dict:
    """Read one persisted pending record from a fresh read-only connection."""
    try:
        resolved = _validate_sender_store(sender_db)
    except Rejected as error:
        return {"ok": False, "error": error.code}
    with closing(_connect_sender(resolved, read_only=True)) as db:
        row = db.execute(
            "SELECT * FROM pending_operations "
            "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
            (dataset_id, source_device_id, operation_id),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": "pending_not_found"}
    return {"ok": True, "record": _row_to_pending_dict(row)}


def _update_pending_state(
    resolved_sender: Path,
    *,
    dataset_id: str,
    source_device_id: str,
    operation_id: str,
    delivery_state: str,
    last_error: str | None,
    raw_response: dict | None,
    confirmed_revision_id: str | None = None,
    confirmed_effect: str | None = None,
) -> dict:
    now = datetime.now(UTC).isoformat()
    raw_json = json.dumps(raw_response, ensure_ascii=False) if raw_response is not None else None
    with closing(_connect_sender(resolved_sender, read_only=False)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "UPDATE pending_operations "
                "SET delivery_state = ?, last_error = ?, last_raw_response_json = ?, "
                "confirmed_revision_id = ?, confirmed_effect = ?, updated_at = ? "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (
                    delivery_state,
                    last_error,
                    raw_json,
                    confirmed_revision_id,
                    confirmed_effect,
                    now,
                    dataset_id,
                    source_device_id,
                    operation_id,
                ),
            )
            row = db.execute(
                "SELECT * FROM pending_operations "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (dataset_id, source_device_id, operation_id),
            ).fetchone()
            db.execute("COMMIT")
            return _row_to_pending_dict(row)
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise


def _receipt_matches_pending(receipt: dict | None, pending: dict) -> bool:
    if not isinstance(receipt, dict):
        return False
    return (
        receipt.get("operation") == pending["operation"]
        and receipt.get("kind") == pending["kind"]
        and receipt.get("canonical_profile") == pending["canonical_profile"]
        and receipt.get("intent_sha256") == pending["intent_sha256"]
        and isinstance(receipt.get("result_revision_id"), str)
        and bool(receipt["result_revision_id"])
    )


def send_pending_operation(
    sender_db: str | Path,
    receiver_db: str | Path,
    *,
    caller: CallerContext,
    operation_id: str,
    fault: str | None = None,
) -> dict:
    """Deliver a persisted pending operation to the isolated receiver and update sender state."""
    try:
        resolved_sender = _validate_sender_store(sender_db)
    except Rejected as error:
        return {"ok": False, "error": error.code}

    fetched = read_pending_operation(
        resolved_sender,
        dataset_id=caller.dataset_id,
        source_device_id=caller.device_id,
        operation_id=operation_id,
    )
    if not fetched["ok"]:
        return fetched
    pending = fetched["record"]
    if pending["delivery_state"] == "confirmed":
        return {"ok": True, "receiver_raw": None, "record": pending}

    # Mark verification_pending before transmission so even a crash mid-send preserves intent.
    pending = _update_pending_state(
        resolved_sender,
        dataset_id=caller.dataset_id,
        source_device_id=caller.device_id,
        operation_id=operation_id,
        delivery_state="verification_pending",
        last_error="in_flight",
        raw_response=pending["last_raw_response"],
    )

    raw_bytes = pending["raw_input_json"].encode("utf-8")
    if fault is not None:
        with tempfile.TemporaryDirectory(prefix="p2-pending-send-") as tmp:
            cmd_file = Path(tmp) / "command.json"
            cmd_file.write_bytes(raw_bytes)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "desktop.expense_executor",
                    "execute",
                    "--database",
                    str(receiver_db),
                    "--input",
                    str(cmd_file),
                    "--dataset-id",
                    caller.dataset_id,
                    "--device-id",
                    caller.device_id,
                    "--fault",
                    fault,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                timeout=10,
                check=False,
            )
            receiver_raw = {
                "exit_code": proc.returncode,
                "stdout_empty": proc.stdout == b"",
                "fault": fault,
            }
        updated = _update_pending_state(
            resolved_sender,
            dataset_id=caller.dataset_id,
            source_device_id=caller.device_id,
            operation_id=operation_id,
            delivery_state="verification_pending",
            last_error="reply_lost",
            raw_response=receiver_raw,
        )
        return {"ok": False, "error": "reply_lost", "receiver_raw": receiver_raw, "record": updated}

    receiver_raw = execute_command(receiver_db, raw_bytes, caller=caller)
    if receiver_raw.get("ok") is True and receiver_raw.get("state") == "committed":
        receipt = receiver_raw.get("receipt")
        if _receipt_matches_pending(receipt, pending):
            updated = _update_pending_state(
                resolved_sender,
                dataset_id=caller.dataset_id,
                source_device_id=caller.device_id,
                operation_id=operation_id,
                delivery_state="confirmed",
                last_error=None,
                raw_response=receiver_raw,
                confirmed_revision_id=receipt["result_revision_id"],
                confirmed_effect=receipt.get("effect"),
            )
            return {"ok": True, "receiver_raw": receiver_raw, "record": updated}
        updated = _update_pending_state(
            resolved_sender,
            dataset_id=caller.dataset_id,
            source_device_id=caller.device_id,
            operation_id=operation_id,
            delivery_state="verification_pending",
            last_error="intent_conflict",
            raw_response=receiver_raw,
        )
        return {
            "ok": False,
            "error": "intent_conflict",
            "receiver_raw": receiver_raw,
            "record": updated,
        }

    raw_error = receiver_raw.get("error") or "unknown_error"
    next_state = "unsent" if raw_error == "unsupported_capability" else "verification_pending"
    updated = _update_pending_state(
        resolved_sender,
        dataset_id=caller.dataset_id,
        source_device_id=caller.device_id,
        operation_id=operation_id,
        delivery_state=next_state,
        last_error=raw_error,
        raw_response=receiver_raw,
    )
    return {"ok": False, "error": raw_error, "receiver_raw": receiver_raw, "record": updated}


def reconcile_pending_operation(
    sender_db: str | Path,
    receiver_db: str | Path,
    *,
    caller: CallerContext,
    operation_id: str,
) -> dict:
    """Query receiver receipt for a persisted pending operation and confirm only on intent match."""
    try:
        resolved_sender = _validate_sender_store(sender_db)
    except Rejected as error:
        return {"ok": False, "error": error.code}

    fetched = read_pending_operation(
        resolved_sender,
        dataset_id=caller.dataset_id,
        source_device_id=caller.device_id,
        operation_id=operation_id,
    )
    if not fetched["ok"]:
        return fetched
    pending = fetched["record"]
    if pending["delivery_state"] == "confirmed":
        return {"ok": True, "receiver_raw": None, "record": pending}

    query_doc = {
        "profile": QUERY_PROFILE,
        "kind": "receipt.lookup",
        "operation": pending["operation"],
    }
    query_bytes = json.dumps(query_doc, ensure_ascii=False).encode("utf-8")
    receiver_raw = lookup_receipt(receiver_db, query_bytes, caller=caller)

    if receiver_raw.get("ok") is True and receiver_raw.get("state") == "committed":
        receipt = receiver_raw.get("receipt")
        if _receipt_matches_pending(receipt, pending):
            updated = _update_pending_state(
                resolved_sender,
                dataset_id=caller.dataset_id,
                source_device_id=caller.device_id,
                operation_id=operation_id,
                delivery_state="confirmed",
                last_error=None,
                raw_response=receiver_raw,
                confirmed_revision_id=receipt["result_revision_id"],
                confirmed_effect=receipt.get("effect"),
            )
            return {"ok": True, "receiver_raw": receiver_raw, "record": updated}
        updated = _update_pending_state(
            resolved_sender,
            dataset_id=caller.dataset_id,
            source_device_id=caller.device_id,
            operation_id=operation_id,
            delivery_state="verification_pending",
            last_error="intent_conflict",
            raw_response=receiver_raw,
        )
        return {
            "ok": False,
            "error": "intent_conflict",
            "receiver_raw": receiver_raw,
            "record": updated,
        }

    if receiver_raw.get("ok") is True:
        state = receiver_raw.get("state")
        reason = receiver_raw.get("reason") or state or "unknown"
        updated = _update_pending_state(
            resolved_sender,
            dataset_id=caller.dataset_id,
            source_device_id=caller.device_id,
            operation_id=operation_id,
            delivery_state="verification_pending",
            last_error=reason,
            raw_response=receiver_raw,
        )
        return {"ok": False, "error": reason, "receiver_raw": receiver_raw, "record": updated}

    raw_error = receiver_raw.get("error") or "unknown_error"
    updated = _update_pending_state(
        resolved_sender,
        dataset_id=caller.dataset_id,
        source_device_id=caller.device_id,
        operation_id=operation_id,
        delivery_state="verification_pending",
        last_error=raw_error,
        raw_response=receiver_raw,
    )
    return {"ok": False, "error": raw_error, "receiver_raw": receiver_raw, "record": updated}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    init_p = subparsers.add_parser("init", help="Initialize a brand-new isolated sender store.")
    init_p.add_argument("--sender-db", type=Path, required=True)

    stage_p = subparsers.add_parser("stage", help="Persist an operation before peer delivery.")
    stage_p.add_argument("--sender-db", type=Path, required=True)
    stage_p.add_argument("--input", type=Path, required=True)

    inspect_p = subparsers.add_parser("inspect", help="Read one persisted pending operation.")
    inspect_p.add_argument("--sender-db", type=Path, required=True)
    inspect_p.add_argument("--dataset-id", default="fixture-dataset")
    inspect_p.add_argument("--device-id", default="fixture-device")
    inspect_p.add_argument("--operation-id", required=True)

    send_p = subparsers.add_parser("send", help="Deliver a pending operation to receiver DB.")
    send_p.add_argument("--sender-db", type=Path, required=True)
    send_p.add_argument("--receiver-db", type=Path, required=True)
    send_p.add_argument("--dataset-id", default="fixture-dataset")
    send_p.add_argument("--device-id", default="fixture-device")
    send_p.add_argument("--operation-id", required=True)
    send_p.add_argument(
        "--fault",
        choices=("before_commit", "after_commit_before_reply"),
        default=None,
    )

    rec_p = subparsers.add_parser("reconcile", help="Reconcile a pending operation via lookup.")
    rec_p.add_argument("--sender-db", type=Path, required=True)
    rec_p.add_argument("--receiver-db", type=Path, required=True)
    rec_p.add_argument("--dataset-id", default="fixture-dataset")
    rec_p.add_argument("--device-id", default="fixture-device")
    rec_p.add_argument("--operation-id", required=True)

    args = parser.parse_args(argv)
    if args.action == "init":
        try:
            init_sender_store(args.sender_db)
        except ValueError as error:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "initialized": True, "sender_db": str(args.sender_db)}))
        return 0

    if args.action == "stage":
        result = stage_pending_operation(args.sender_db, args.input.read_bytes())
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if args.action == "inspect":
        result = read_pending_operation(
            args.sender_db,
            dataset_id=args.dataset_id,
            source_device_id=args.device_id,
            operation_id=args.operation_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    caller = CallerContext.for_fixture(dataset_id=args.dataset_id, device_id=args.device_id)
    if args.action == "send":
        result = send_pending_operation(
            args.sender_db,
            args.receiver_db,
            caller=caller,
            operation_id=args.operation_id,
            fault=args.fault,
        )
    else:
        result = reconcile_pending_operation(
            args.sender_db,
            args.receiver_db,
            caller=caller,
            operation_id=args.operation_id,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
