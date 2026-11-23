"""Isolated SQLite persistent executor and receipt lookup for Phase 2 expense.create candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

CANONICAL_PROFILE = "p1-host-intent-c14n-1"
CONTROL_PROFILE = "p1-host-control-1"
QUERY_PROFILE = "p1-host-query-1"
SAFE_INTEGER = 2**53 - 1
MAX_CANONICAL_BYTES = 65536

OPERATION_KEYS = frozenset({"dataset_id", "source_device_id", "operation_id"})
TARGET_KEYS = frozenset({"travel_id", "branch_id", "entity_id"})
EXPENSE_CREATE_FIELDS = frozenset(
    {
        "title",
        "notes",
        "currency",
        "amount_cents",
        "paid_on",
        "category",
        "day",
        "activity_id",
        "receipt_ids",
        "allow_shared_receipts",
        "budget_item_id",
        "settles_budget",
    }
)
EXPENSE_CATEGORIES = frozenset({"transport", "lodging", "meal", "ticket", "shopping", "other"})

SUPPORTED_CAPABILITIES = {
    "canonical_profile": CANONICAL_PROFILE,
    "control_profile": CONTROL_PROFILE,
    "query_profile": QUERY_PROFILE,
    "commands": ("expense.create",),
    "queries": ("receipt.lookup",),
    "currencies": ("CNY",),
    "paid_on": "null_only",
    "receipt_ids": "empty_only",
    "budget_item_id": "null_only",
    "categories": tuple(sorted(EXPENSE_CATEGORIES)),
}

NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    travel_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branches (
    dataset_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    head_revision_id TEXT NOT NULL,
    PRIMARY KEY (dataset_id, travel_id, branch_id)
);

CREATE TABLE IF NOT EXISTS revisions (
    dataset_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    parent_revision_id TEXT,
    seq INTEGER NOT NULL,
    source_operation_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, travel_id, branch_id, revision_id),
    UNIQUE (dataset_id, travel_id, branch_id, seq)
);

CREATE TABLE IF NOT EXISTS expenses (
    dataset_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    title TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 1 AND amount_cents <= 9999999999),
    currency TEXT NOT NULL,
    paid_on TEXT,
    category TEXT NOT NULL,
    day INTEGER,
    activity_id TEXT,
    notes TEXT NOT NULL,
    receipt_ids_json TEXT NOT NULL,
    budget_item_id TEXT,
    settles_budget INTEGER NOT NULL,
    created_by_operation_id TEXT NOT NULL,
    PRIMARY KEY (dataset_id, travel_id, branch_id, entity_id)
);

CREATE TABLE IF NOT EXISTS operation_receipts (
    dataset_id TEXT NOT NULL,
    source_device_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    canonical_profile TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    canonical_intent_json TEXT NOT NULL,
    effect TEXT NOT NULL,
    result_revision_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, source_device_id, operation_id)
);

CREATE TABLE IF NOT EXISTS outbox (
    dataset_id TEXT NOT NULL,
    source_device_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    canonical_profile TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    canonical_intent_json TEXT NOT NULL,
    result_revision_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, source_device_id, operation_id)
);
"""


class Rejected(Exception):
    """Deterministic protocol or capability rejection with stable error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CallerContext:
    """Receiver-verified caller identity and scope. Never trusted from payload alone."""

    dataset_id: str
    device_id: str
    authorized_datasets: frozenset[str] = field(default_factory=frozenset)
    authorized_devices: frozenset[str] = field(default_factory=frozenset)
    can_write: bool = True
    can_lookup_receipt: bool = True

    @classmethod
    def for_fixture(
        cls,
        *,
        dataset_id: str = "fixture-dataset",
        device_id: str = "fixture-device",
        can_write: bool = True,
        can_lookup_receipt: bool = True,
    ) -> CallerContext:
        return cls(
            dataset_id=dataset_id,
            device_id=device_id,
            authorized_datasets=frozenset({dataset_id}),
            authorized_devices=frozenset({device_id}),
            can_write=can_write,
            can_lookup_receipt=can_lookup_receipt,
        )


class _TokenReader:
    """Strict RFC 8259 token reader matching p1_json_review.TokenReader."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def _whitespace(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _take(self, expected: str) -> None:
        self._whitespace()
        if not self.text.startswith(expected, self.pos):
            raise Rejected("json")
        self.pos += len(expected)

    def _string(self) -> str:
        self._whitespace()
        if not self.text.startswith('"', self.pos):
            raise Rejected("json")
        try:
            value, self.pos = json.decoder.scanstring(self.text, self.pos + 1, True)
            return value
        except (ValueError, json.JSONDecodeError):
            raise Rejected("json") from None

    def _value(self):
        self._whitespace()
        if self.pos == len(self.text):
            raise Rejected("json")
        char = self.text[self.pos]
        if char == '"':
            return ["string", self._string()]
        if char == "{":
            self.pos += 1
            pairs = []
            self._whitespace()
            if not self.text.startswith("}", self.pos):
                while True:
                    key = self._string()
                    self._take(":")
                    pairs.append([key, self._value()])
                    self._whitespace()
                    if self.text.startswith("}", self.pos):
                        break
                    self._take(",")
            self._take("}")
            return ["object", pairs]
        if char == "[":
            self.pos += 1
            items = []
            self._whitespace()
            if not self.text.startswith("]", self.pos):
                while True:
                    items.append(self._value())
                    self._whitespace()
                    if self.text.startswith("]", self.pos):
                        break
                    self._take(",")
            self._take("]")
            return ["array", items]
        for spelling, result in (
            ("true", ["boolean", True]),
            ("false", ["boolean", False]),
            ("null", ["null"]),
        ):
            if self.text.startswith(spelling, self.pos):
                self.pos += len(spelling)
                return result
        match = NUMBER_RE.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            return ["number", match[0]]
        raise Rejected("json")

    def document(self):
        tree = self._value()
        self._whitespace()
        if self.pos != len(self.text):
            raise Rejected("json")
        return tree


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise Rejected(code)


def _check_scalar(value: str) -> None:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise Rejected("unicode")


def _check_tree(node) -> None:
    tag = node[0]
    if tag == "string":
        _check_scalar(node[1])
    elif tag == "array":
        for item in node[1]:
            _check_tree(item)
    elif tag == "object":
        seen = set()
        for key, value in node[1]:
            _check_scalar(key)
            if key in seen:
                raise Rejected("duplicate_key")
            seen.add(key)
            _check_tree(value)


def _parse_exact_integer(token: str) -> int:
    _require(len(token) <= 64, "resource_number")
    mantissa, _, exponent = token.lower().partition("e")
    whole, _, fraction = mantissa.lstrip("-").partition(".")
    coefficient = int(whole + fraction)
    if coefficient == 0:
        return 0
    power = int(exponent or "0") - len(fraction)
    _require(1 <= len(str(coefficient)) + power <= 16, "integer_range")
    if power < 0:
        coefficient, remainder = divmod(coefficient, 10**-power)
        _require(remainder == 0, "integer_range")
    else:
        coefficient *= 10**power
    _require(coefficient <= SAFE_INTEGER, "integer_range")
    return -coefficient if mantissa.startswith("-") else coefficient


def _read_canonical_value(node):
    tag = node[0]
    if tag == "object":
        pairs = sorted(node[1], key=lambda pair: pair[0].encode("utf-16-be"))
        return {key: _read_canonical_value(value) for key, value in pairs}
    if tag == "array":
        return [_read_canonical_value(value) for value in node[1]]
    if tag == "number":
        return _parse_exact_integer(node[1])
    return None if tag == "null" else node[1]


def _check_shape(value, required: frozenset[str]) -> None:
    _require(isinstance(value, dict) and value.keys() == required, "shape")


def _check_identifiers(value, keys: frozenset[str]) -> None:
    _check_shape(value, keys)
    for item in value.values():
        _require(
            isinstance(item, str) and 1 <= len(item) <= 256 and len(item.encode()) <= 1024,
            "identity",
        )


def canonicalize_intent(raw: bytes, channel: str) -> tuple[dict, bytes, str]:
    """Decode, validate envelope, and produce p1-host-intent-c14n-1 bytes and SHA-256."""
    _require(channel in ("control", "receipt-query"), "canonical_channel")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Rejected("encoding") from None
    tree = _TokenReader(text).document()
    _check_tree(tree)
    value = _read_canonical_value(tree)

    is_query = channel == "receipt-query"
    base_keys = frozenset({"profile", "kind", "operation"})
    expected_keys = (
        base_keys
        if is_query
        else base_keys | frozenset({"target", "expected_revision_id", "payload"})
    )
    _check_shape(value, expected_keys)
    _check_identifiers(value["operation"], OPERATION_KEYS)
    _require(
        value["profile"] == (QUERY_PROFILE if is_query else CONTROL_PROFILE),
        "unsupported_profile",
    )

    if is_query:
        _require(value["kind"] == "receipt.lookup", "unsupported_command")
    else:
        _require(value["kind"] == "expense.create", "unsupported_command")
        _check_identifiers(value["target"], TARGET_KEYS)
        _check_identifiers({"revision": value["expected_revision_id"]}, frozenset({"revision"}))
        payload = value["payload"]
        _check_shape(payload, EXPENSE_CREATE_FIELDS)
        _require(type(payload["amount_cents"]) is int, "type")
        _require(1 <= payload["amount_cents"] <= 9_999_999_999, "integer_range")
        if payload["day"] is not None:
            _require(type(payload["day"]) is int, "type")
            _require(1 <= payload["day"] <= 60, "integer_range")

    document = {
        "canonical_profile": CANONICAL_PROFILE,
        "channel": channel,
        "intent": value,
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _require(len(encoded) <= MAX_CANONICAL_BYTES, "canonical_bytes")
    return value, encoded, hashlib.sha256(encoded).hexdigest()


def _validate_expense_create_capabilities(payload: dict) -> None:
    title = payload["title"]
    _require(isinstance(title, str), "type")
    _require(1 <= len(title) <= 100 and title.strip() != "", "invalid_field")
    _require(not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in title), "invalid_field")

    notes = payload["notes"]
    _require(isinstance(notes, str) and len(notes) <= 1000, "invalid_field")
    _require(
        not any(
            (ord(ch) < 0x20 and ch not in ("\t", "\n", "\r")) or 0x7F <= ord(ch) <= 0x9F
            for ch in notes
        ),
        "invalid_field",
    )

    _require(isinstance(payload["currency"], str), "type")
    _require(payload["currency"] == "CNY", "unsupported_capability")
    _require(payload["paid_on"] is None, "unsupported_capability")

    _require(isinstance(payload["category"], str), "type")
    _require(payload["category"] in EXPENSE_CATEGORIES, "invalid_field")

    activity_id = payload["activity_id"]
    if activity_id is not None:
        _require(isinstance(activity_id, str) and 1 <= len(activity_id) <= 100, "invalid_field")

    receipt_ids = payload["receipt_ids"]
    _require(isinstance(receipt_ids, list), "type")
    _require(len(receipt_ids) == 0, "unsupported_capability")

    _require(type(payload["allow_shared_receipts"]) is bool, "type")
    _require(payload["budget_item_id"] is None, "unsupported_capability")
    _require(type(payload["settles_budget"]) is bool, "type")
    _require(not payload["settles_budget"], "invalid_field")


def _authorize(caller: CallerContext, operation: dict, *, require_write: bool) -> None:
    if require_write and not caller.can_write:
        raise Rejected("unauthorized")
    if not require_write and not caller.can_lookup_receipt:
        raise Rejected("unauthorized")
    dataset_id = operation["dataset_id"]
    device_id = operation["source_device_id"]
    if dataset_id != caller.dataset_id or dataset_id not in caller.authorized_datasets:
        raise Rejected("unauthorized")
    if device_id != caller.device_id or device_id not in caller.authorized_devices:
        raise Rejected("unauthorized")


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_isolated_store(
    db_path: str | Path,
    *,
    dataset_id: str = "fixture-dataset",
    travel_id: str = "fixture-travel",
    branch_id: str = "fixture-branch",
    initial_revision_id: str = "fixture-r0",
) -> None:
    """Initialize a fresh isolated SQLite store seeded with one empty branch head."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    with closing(_connect(path)) as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT OR IGNORE INTO datasets(dataset_id, travel_id) VALUES (?, ?)",
            (dataset_id, travel_id),
        )
        db.execute(
            "INSERT OR IGNORE INTO branches(dataset_id, travel_id, branch_id, head_revision_id) "
            "VALUES (?, ?, ?, ?)",
            (dataset_id, travel_id, branch_id, initial_revision_id),
        )
        db.execute(
            "INSERT OR IGNORE INTO revisions("
            "dataset_id, travel_id, branch_id, revision_id, parent_revision_id, "
            "seq, source_operation_id, created_at"
            ") VALUES (?, ?, ?, ?, NULL, 0, NULL, ?)",
            (dataset_id, travel_id, branch_id, initial_revision_id, now),
        )
        db.execute("COMMIT")


def _format_receipt_row(row: sqlite3.Row) -> dict:
    return {
        "operation": {
            "dataset_id": row["dataset_id"],
            "source_device_id": row["source_device_id"],
            "operation_id": row["operation_id"],
        },
        "kind": row["kind"],
        "canonical_profile": row["canonical_profile"],
        "intent_sha256": row["intent_sha256"],
        "effect": row["effect"],
        "result_revision_id": row["result_revision_id"],
        "travel_id": row["travel_id"],
        "branch_id": row["branch_id"],
        "entity_id": row["entity_id"],
    }


def lookup_receipt(db_path: str | Path, raw_query: bytes, *, caller: CallerContext) -> dict:
    """Authorize caller first, then query committed operation receipt by operation key."""
    try:
        if not caller.can_lookup_receipt:
            raise Rejected("unauthorized")
        query, _, _ = canonicalize_intent(raw_query, "receipt-query")
        op = query["operation"]
        _authorize(caller, op, require_write=False)
    except Rejected as error:
        return {"ok": False, "error": error.code}

    with closing(_connect(db_path)) as db:
        row = db.execute(
            "SELECT dataset_id, source_device_id, operation_id, kind, canonical_profile, "
            "intent_sha256, effect, result_revision_id, travel_id, branch_id, entity_id "
            "FROM operation_receipts "
            "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
            (op["dataset_id"], op["source_device_id"], op["operation_id"]),
        ).fetchone()
    if row is None:
        return {"ok": True, "state": "not_found", "receipt": None}
    return {"ok": True, "state": "committed", "receipt": _format_receipt_row(row)}


def _begin_immediate_with_retry(db: sqlite3.Connection, *, retries: int = 20) -> None:
    for attempt in range(retries):
        try:
            db.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or attempt == retries - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def execute_command(
    db_path: str | Path,
    raw_command: bytes,
    *,
    caller: CallerContext,
    enqueue_outbox: bool = True,
    on_before_commit=None,
    on_after_commit_before_reply=None,
) -> dict:
    """Execute one candidate expense.create command atomically on isolated SQLite."""
    try:
        if not caller.can_write:
            raise Rejected("unauthorized")
        command, canonical_bytes, intent_sha256 = canonicalize_intent(raw_command, "control")
        op = command["operation"]
        _authorize(caller, op, require_write=True)
    except Rejected as error:
        return {"ok": False, "error": error.code}

    canonical_text = canonical_bytes.decode("utf-8")
    target = command["target"]
    payload = command["payload"]

    with closing(_connect(db_path)) as db:
        _begin_immediate_with_retry(db)
        try:
            existing = db.execute(
                "SELECT dataset_id, source_device_id, operation_id, kind, canonical_profile, "
                "intent_sha256, canonical_intent_json, effect, result_revision_id, "
                "travel_id, branch_id, entity_id "
                "FROM operation_receipts "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (op["dataset_id"], op["source_device_id"], op["operation_id"]),
            ).fetchone()

            if existing is not None:
                db.execute("ROLLBACK")
                if (
                    existing["intent_sha256"] == intent_sha256
                    and existing["canonical_intent_json"] == canonical_text
                ):
                    return {
                        "ok": True,
                        "replayed": True,
                        "state": "committed",
                        "receipt": _format_receipt_row(existing),
                    }
                return {"ok": False, "error": "intent_conflict"}

            try:
                _validate_expense_create_capabilities(payload)
            except Rejected as error:
                db.execute("ROLLBACK")
                return {"ok": False, "error": error.code}

            branch = db.execute(
                "SELECT head_revision_id FROM branches "
                "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ?",
                (op["dataset_id"], target["travel_id"], target["branch_id"]),
            ).fetchone()
            if branch is None:
                db.execute("ROLLBACK")
                return {"ok": False, "error": "target_not_found"}

            current_head = branch["head_revision_id"]
            if current_head != command["expected_revision_id"]:
                db.execute("ROLLBACK")
                return {
                    "ok": False,
                    "error": "baseline_conflict",
                    "current_revision_id": current_head,
                }

            existing_entity = db.execute(
                "SELECT 1 FROM expenses "
                "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ? AND entity_id = ?",
                (
                    op["dataset_id"],
                    target["travel_id"],
                    target["branch_id"],
                    target["entity_id"],
                ),
            ).fetchone()
            if existing_entity is not None:
                db.execute("ROLLBACK")
                return {"ok": False, "error": "entity_exists"}

            max_seq = db.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM revisions "
                "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ?",
                (op["dataset_id"], target["travel_id"], target["branch_id"]),
            ).fetchone()["max_seq"]
            next_seq = max_seq + 1
            result_revision_id = f"rev-{next_seq}-{uuid.uuid4().hex[:12]}"
            now = datetime.now(UTC).isoformat()

            db.execute(
                "INSERT INTO revisions("
                "dataset_id, travel_id, branch_id, revision_id, parent_revision_id, "
                "seq, source_operation_id, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    op["dataset_id"],
                    target["travel_id"],
                    target["branch_id"],
                    result_revision_id,
                    current_head,
                    next_seq,
                    op["operation_id"],
                    now,
                ),
            )
            db.execute(
                "UPDATE branches SET head_revision_id = ? "
                "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ?",
                (
                    result_revision_id,
                    op["dataset_id"],
                    target["travel_id"],
                    target["branch_id"],
                ),
            )
            db.execute(
                "INSERT INTO expenses("
                "dataset_id, travel_id, branch_id, entity_id, revision_id, title, "
                "amount_cents, currency, paid_on, category, day, activity_id, notes, "
                "receipt_ids_json, budget_item_id, settles_budget, created_by_operation_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    op["dataset_id"],
                    target["travel_id"],
                    target["branch_id"],
                    target["entity_id"],
                    result_revision_id,
                    payload["title"],
                    payload["amount_cents"],
                    payload["currency"],
                    payload["paid_on"],
                    payload["category"],
                    payload["day"],
                    payload["activity_id"],
                    payload["notes"],
                    json.dumps(payload["receipt_ids"], ensure_ascii=False, separators=(",", ":")),
                    payload["budget_item_id"],
                    int(payload["settles_budget"]),
                    op["operation_id"],
                ),
            )
            db.execute(
                "INSERT INTO operation_receipts("
                "dataset_id, source_device_id, operation_id, kind, canonical_profile, "
                "intent_sha256, canonical_intent_json, effect, result_revision_id, "
                "travel_id, branch_id, entity_id, committed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'changed', ?, ?, ?, ?, ?)",
                (
                    op["dataset_id"],
                    op["source_device_id"],
                    op["operation_id"],
                    command["kind"],
                    CANONICAL_PROFILE,
                    intent_sha256,
                    canonical_text,
                    result_revision_id,
                    target["travel_id"],
                    target["branch_id"],
                    target["entity_id"],
                    now,
                ),
            )
            if enqueue_outbox:
                db.execute(
                    "INSERT INTO outbox("
                    "dataset_id, source_device_id, operation_id, kind, canonical_profile, "
                    "intent_sha256, canonical_intent_json, result_revision_id, state, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        op["dataset_id"],
                        op["source_device_id"],
                        op["operation_id"],
                        command["kind"],
                        CANONICAL_PROFILE,
                        intent_sha256,
                        canonical_text,
                        result_revision_id,
                        now,
                    ),
                )

            if on_before_commit is not None:
                on_before_commit()
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise

    if on_after_commit_before_reply is not None:
        on_after_commit_before_reply()

    return {
        "ok": True,
        "replayed": False,
        "state": "committed",
        "receipt": {
            "operation": dict(op),
            "kind": command["kind"],
            "canonical_profile": CANONICAL_PROFILE,
            "intent_sha256": intent_sha256,
            "effect": "changed",
            "result_revision_id": result_revision_id,
            "travel_id": target["travel_id"],
            "branch_id": target["branch_id"],
            "entity_id": target["entity_id"],
        },
    }


def _make_fault_hook(mode: str | None, exit_code: int):
    if mode is None:
        return None

    def _abort():
        os._exit(exit_code)

    return _abort


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    init_p = subparsers.add_parser("init", help="Initialize an isolated SQLite store.")
    init_p.add_argument("--database", type=Path, required=True)
    init_p.add_argument("--dataset-id", default="fixture-dataset")
    init_p.add_argument("--travel-id", default="fixture-travel")
    init_p.add_argument("--branch-id", default="fixture-branch")
    init_p.add_argument("--initial-revision-id", default="fixture-r0")

    exec_p = subparsers.add_parser("execute", help="Execute a control command JSON file.")
    exec_p.add_argument("--database", type=Path, required=True)
    exec_p.add_argument("--input", type=Path, required=True)
    exec_p.add_argument("--dataset-id", default="fixture-dataset")
    exec_p.add_argument("--device-id", default="fixture-device")
    exec_p.add_argument(
        "--fault",
        choices=("before_commit", "after_commit_before_reply"),
        default=None,
    )
    exec_p.add_argument("--fault-exit-code", type=int, default=86)

    lookup_p = subparsers.add_parser("lookup", help="Query a persistent operation receipt.")
    lookup_p.add_argument("--database", type=Path, required=True)
    lookup_p.add_argument("--input", type=Path, required=True)
    lookup_p.add_argument("--dataset-id", default="fixture-dataset")
    lookup_p.add_argument("--device-id", default="fixture-device")

    args = parser.parse_args(argv)
    if args.action == "init":
        init_isolated_store(
            args.database,
            dataset_id=args.dataset_id,
            travel_id=args.travel_id,
            branch_id=args.branch_id,
            initial_revision_id=args.initial_revision_id,
        )
        print(json.dumps({"initialized": True, "database": str(args.database)}))
        return 0

    caller = CallerContext.for_fixture(dataset_id=args.dataset_id, device_id=args.device_id)
    raw = args.input.read_bytes()
    if args.action == "execute":
        result = execute_command(
            args.database,
            raw,
            caller=caller,
            on_before_commit=_make_fault_hook(
                "before_commit" if args.fault == "before_commit" else None,
                args.fault_exit_code,
            ),
            on_after_commit_before_reply=_make_fault_hook(
                "after_commit_before_reply" if args.fault == "after_commit_before_reply" else None,
                args.fault_exit_code,
            ),
        )
    else:
        result = lookup_receipt(args.database, raw, caller=caller)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
