"""Desktop receive path for frozen travelflow.expense-create 0.1.0 into real trip ledgers.

Transaction boundary note:
- Existing routes_itinerary._save_revision commits LangGraph checkpoints, versions, plans,
  and sessions across separate SQLite transactions/connections.
- To preserve atomic consistency between the new trip version (versions + plans + sessions)
  and the durable operation receipt (exchange_operation_receipts), this service commits all
  four business/receipt tables in a single BEGIN IMMEDIATE ... COMMIT transaction on
  SessionStore's SQLite connection BEFORE any optional LangGraph checkpoint sync.
- LangGraph AsyncSqliteSaver owns a separate SQLite connection; if a crash occurs after the
  atomic business+receipt commit and before optional graph.aupdate_state finishes, trip detail
  and ledger reads (versions/plans) and receipt lookup/replay remain 100% consistent while
  the LangGraph checkpoint lags until the next chat/sync turn.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.agents.checkpoint import thread_config
from app.domain.enums import PlanStatus
from app.domain.itinerary import ExpenseCreate, Itinerary
from app.domain.models import Requirements
from app.services.branch_lock import branch_guard
from app.services.itinerary_service import sync_markdown
from app.services.travel_service import add_expense
from app.storage.session_store import SessionStore

CONTRACT_ID = "travelflow.expense-create"
CONTRACT_VERSION = "0.1.0"
CANONICAL_PROFILE = "p1-host-intent-c14n-1"
CONTROL_PROFILE = "p1-host-control-1"
QUERY_PROFILE = "p1-host-query-1"
SAFE_INTEGER = 2**53 - 1
MAX_CANONICAL_BYTES = 65536
MAX_BRANCH_EXPENSES = 2000

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
NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


class ExchangeRejected(Exception):
    """Deterministic contract rejection with stable error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExchangeReceiveScope:
    """Explicit receiver-verified identity and dataset scope."""

    dataset_id: str
    authorized_devices: frozenset[str]
    can_write: bool = True
    can_lookup_receipt: bool = True


class _TokenReader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def _whitespace(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _take(self, expected: str) -> None:
        self._whitespace()
        if not self.text.startswith(expected, self.pos):
            raise ExchangeRejected("json")
        self.pos += len(expected)

    def _string(self) -> str:
        self._whitespace()
        if not self.text.startswith('"', self.pos):
            raise ExchangeRejected("json")
        try:
            scan = json.decoder.scanstring  # type: ignore[attr-defined]
            value, self.pos = scan(self.text, self.pos + 1, True)
            return str(value)
        except (ValueError, json.JSONDecodeError):
            raise ExchangeRejected("json") from None

    def _value(self) -> Any:
        self._whitespace()
        if self.pos == len(self.text):
            raise ExchangeRejected("json")
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
        raise ExchangeRejected("json")

    def document(self) -> Any:
        tree = self._value()
        self._whitespace()
        if self.pos != len(self.text):
            raise ExchangeRejected("json")
        return tree


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ExchangeRejected(code)


def _check_scalar(value: str) -> None:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise ExchangeRejected("unicode")


def _check_tree(node: Any) -> None:
    tag = node[0]
    if tag == "string":
        _check_scalar(node[1])
    elif tag == "array":
        for item in node[1]:
            _check_tree(item)
    elif tag == "object":
        seen: set[str] = set()
        for key, value in node[1]:
            _check_scalar(key)
            if key in seen:
                raise ExchangeRejected("duplicate_key")
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


def _read_canonical_value(node: Any) -> Any:
    tag = node[0]
    if tag == "object":
        pairs = sorted(node[1], key=lambda pair: pair[0].encode("utf-16-be"))
        return {key: _read_canonical_value(value) for key, value in pairs}
    if tag == "array":
        return [_read_canonical_value(value) for value in node[1]]
    if tag == "number":
        return _parse_exact_integer(node[1])
    return None if tag == "null" else node[1]


def _check_shape(value: Any, required: frozenset[str]) -> None:
    _require(isinstance(value, dict) and value.keys() == required, "shape")


def _check_identifiers(value: Any, keys: frozenset[str]) -> None:
    _check_shape(value, keys)
    for item in value.values():
        _require(
            isinstance(item, str) and 1 <= len(item) <= 256 and len(item.encode()) <= 1024,
            "identity",
        )


def canonicalize_exchange_intent(raw: bytes, channel: str) -> tuple[dict[str, Any], bytes, str]:
    """Decode and canonicalize a 0.1.0 control or receipt-query envelope."""
    _require(channel in ("control", "receipt-query"), "canonical_channel")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ExchangeRejected("encoding") from None
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


def _validate_v010_capabilities(payload: dict[str, Any]) -> None:
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
    _require(payload["day"] is None, "unsupported_capability")
    _require(payload["activity_id"] is None, "unsupported_capability")
    _require(isinstance(payload["receipt_ids"], list), "type")
    _require(len(payload["receipt_ids"]) == 0, "unsupported_capability")
    _require(type(payload["allow_shared_receipts"]) is bool, "type")
    _require(payload["budget_item_id"] is None, "unsupported_capability")
    _require(type(payload["settles_budget"]) is bool, "type")
    _require(not payload["settles_budget"], "invalid_field")


def _authorize_scope(
    scope: ExchangeReceiveScope, operation: dict[str, Any], *, require_write: bool
) -> None:
    if require_write and not scope.can_write:
        raise ExchangeRejected("unauthorized")
    if not require_write and not scope.can_lookup_receipt:
        raise ExchangeRejected("unauthorized")
    if operation["dataset_id"] != scope.dataset_id:
        raise ExchangeRejected("unauthorized")
    if operation["source_device_id"] not in scope.authorized_devices:
        raise ExchangeRejected("unauthorized")


def _format_receipt(row: Any) -> dict[str, Any]:
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
        "result_revision_id": row["result_version_id"],
        "travel_id": row["session_id"],
        "branch_id": row["branch_id"],
        "entity_id": row["entity_id"],
        "origin_direction": row["origin_direction"],
    }


async def _begin_immediate(conn: Any) -> bool:
    try:
        await conn.execute("BEGIN IMMEDIATE")
        return True
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return False
        raise


async def lookup_exchange_receipt(
    store: SessionStore,
    raw_query: bytes,
    *,
    scope: ExchangeReceiveScope,
) -> dict[str, Any]:
    """Authorize caller first, then query durable exchange receipt on the real app DB."""
    try:
        if not scope.can_lookup_receipt:
            raise ExchangeRejected("unauthorized")
        query, _, _ = canonicalize_exchange_intent(raw_query, "receipt-query")
        op = query["operation"]
        _authorize_scope(scope, op, require_write=False)
    except ExchangeRejected as error:
        return {"ok": False, "error": error.code}

    conn = store._conn
    if not await _begin_immediate(conn):
        return {"ok": True, "state": "unknown", "reason": "storage_busy"}
    try:
        cursor = await conn.execute(
            "SELECT dataset_id, source_device_id, operation_id, kind, canonical_profile, "
            "intent_sha256, effect, result_version_id, session_id, branch_id, entity_id, "
            "origin_direction FROM exchange_operation_receipts "
            "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
            (op["dataset_id"], op["source_device_id"], op["operation_id"]),
        )
        row = await cursor.fetchone()
    finally:
        await conn.execute("ROLLBACK")

    if row is None:
        return {"ok": True, "state": "not_found_at_settled_read", "receipt": None}
    return {"ok": True, "state": "committed", "receipt": _format_receipt(row)}


async def receive_expense_create(
    store: SessionStore,
    raw_command: bytes,
    *,
    scope: ExchangeReceiveScope,
    graph: Any = None,
    on_before_commit: Callable[[], None] | None = None,
    on_after_commit_before_reply: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Receive one 0.1.0 expense.create command into a real trip branch atomically."""
    try:
        if not scope.can_write:
            raise ExchangeRejected("unauthorized")
        command, canonical_bytes, intent_sha256 = canonicalize_exchange_intent(
            raw_command, "control"
        )
        op = command["operation"]
        _authorize_scope(scope, op, require_write=True)
    except ExchangeRejected as error:
        return {"ok": False, "error": error.code}

    target = command["target"]
    payload = command["payload"]
    canonical_text = canonical_bytes.decode("utf-8")
    conn = store._conn

    async with branch_guard(target["branch_id"]):
        if not await _begin_immediate(conn):
            return {"ok": False, "error": "storage_busy"}
        checkpoint_sync: tuple[str, str, Itinerary, Requirements, str, list[Any]] | None = None
        try:
            # 1. Verify target (session_id, branch_id) before receipt lookup.
            cur = await conn.execute(
                "SELECT id, session_id FROM branches WHERE id = ? AND session_id = ?",
                (target["branch_id"], target["travel_id"]),
            )
            branch_row = await cur.fetchone()
            if branch_row is None:
                await conn.execute("ROLLBACK")
                return {"ok": False, "error": "unauthorized"}

            # 2. Check durable receipt by (dataset_id, source_device_id, operation_id).
            cur = await conn.execute(
                "SELECT dataset_id, source_device_id, operation_id, kind, canonical_profile, "
                "intent_sha256, canonical_intent_json, effect, result_version_id, session_id, "
                "branch_id, entity_id, origin_direction FROM exchange_operation_receipts "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (op["dataset_id"], op["source_device_id"], op["operation_id"]),
            )
            existing = await cur.fetchone()
            if existing is not None:
                await conn.execute("ROLLBACK")
                if (
                    existing["intent_sha256"] == intent_sha256
                    and existing["canonical_intent_json"] == canonical_text
                ):
                    return {
                        "ok": True,
                        "replayed": True,
                        "state": "committed",
                        "receipt": _format_receipt(existing),
                    }
                return {"ok": False, "error": "intent_conflict"}

            # 3. Validate 0.1.0 capabilities and field rules.
            try:
                _validate_v010_capabilities(payload)
            except ExchangeRejected as error:
                await conn.execute("ROLLBACK")
                return {"ok": False, "error": error.code}

            # 4. Read current branch head version + plan.
            cur = await conn.execute(
                "SELECT id, seq, detail_level, checkpoint_id FROM versions "
                "WHERE branch_id = ? ORDER BY seq DESC LIMIT 1",
                (target["branch_id"],),
            )
            head_row = await cur.fetchone()
            if head_row is None:
                await conn.execute("ROLLBACK")
                return {"ok": False, "error": "baseline_conflict", "current_revision_id": None}

            current_head_id = str(head_row["id"])
            if current_head_id != command["expected_revision_id"]:
                await conn.execute("ROLLBACK")
                return {
                    "ok": False,
                    "error": "baseline_conflict",
                    "current_revision_id": current_head_id,
                }

            cur = await conn.execute(
                "SELECT markdown, citations, todos, searched_at, itinerary, requirements "
                "FROM plans WHERE version_id = ?",
                (current_head_id,),
            )
            plan_row = await cur.fetchone()
            if plan_row is None or not plan_row["itinerary"]:
                await conn.execute("ROLLBACK")
                return {"ok": False, "error": "invalid_target_state"}

            itinerary = Itinerary.model_validate_json(plan_row["itinerary"])
            requirements = (
                Requirements.model_validate_json(plan_row["requirements"])
                if plan_row["requirements"]
                else Requirements()
            )

            # 5. Enforce entity uniqueness and 2000-expense single-branch cap.
            if any(entry.id == target["entity_id"] for entry in itinerary.expenses):
                await conn.execute("ROLLBACK")
                return {"ok": False, "error": "entity_exists"}
            if len(itinerary.expenses) >= MAX_BRANCH_EXPENSES:
                await conn.execute("ROLLBACK")
                return {"ok": False, "error": "expense_limit_exceeded"}

            # 6. Apply existing domain add_expense + sync_markdown.
            expense_create = ExpenseCreate(
                request_id=target["entity_id"],
                title=payload["title"],
                amount=Decimal(payload["amount_cents"]).scaleb(-2),
                category=payload["category"],
                day=payload["day"],
                activity_id=payload["activity_id"],
                notes=payload["notes"],
                receipt_ids=payload["receipt_ids"],
                allow_shared_receipts=payload["allow_shared_receipts"],
                budget_item_id=payload["budget_item_id"],
                settles_budget=payload["settles_budget"],
            )
            add_expense(itinerary, expense_create)
            new_markdown = sync_markdown(plan_row["markdown"] or "", itinerary, requirements)

            new_version_id = uuid.uuid4().hex[:12]
            next_seq = int(head_row["seq"]) + 1
            now_local = datetime.now().isoformat(timespec="seconds")
            now_utc = datetime.now(UTC).isoformat()

            # 7. Atomically commit versions + plans + sessions + exchange_operation_receipts.
            # Received events are tagged origin_direction='received' and never enqueued as
            # local outbound edits to echo back to the source device.
            await conn.execute(
                "INSERT INTO versions(id, branch_id, seq, summary, detail_level, trigger, "
                "checkpoint_id, created_at) VALUES (?, ?, ?, ?, ?, 'exchange_receive', ?, ?)",
                (
                    new_version_id,
                    target["branch_id"],
                    next_seq,
                    f"同步记账：{payload['title']}",
                    head_row["detail_level"],
                    head_row["checkpoint_id"],
                    now_local,
                ),
            )
            await conn.execute(
                "INSERT INTO plans(version_id, markdown, citations, todos, searched_at, "
                "itinerary, requirements) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_version_id,
                    new_markdown,
                    plan_row["citations"],
                    plan_row["todos"],
                    plan_row["searched_at"],
                    itinerary.model_dump_json(),
                    requirements.model_dump_json(),
                ),
            )
            await conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now_local, target["travel_id"]),
            )
            await conn.execute(
                "INSERT INTO exchange_operation_receipts("
                "dataset_id, source_device_id, operation_id, kind, canonical_profile, "
                "intent_sha256, canonical_intent_json, effect, result_version_id, "
                "session_id, branch_id, entity_id, origin_direction, committed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'changed', ?, ?, ?, ?, 'received', ?)",
                (
                    op["dataset_id"],
                    op["source_device_id"],
                    op["operation_id"],
                    command["kind"],
                    CANONICAL_PROFILE,
                    intent_sha256,
                    canonical_text,
                    new_version_id,
                    target["travel_id"],
                    target["branch_id"],
                    target["entity_id"],
                    now_utc,
                ),
            )

            if on_before_commit is not None:
                on_before_commit()
            await conn.execute("COMMIT")
            todos_raw = json.loads(plan_row["todos"] or "[]")
            checkpoint_sync = (
                target["branch_id"],
                new_version_id,
                itinerary,
                requirements,
                new_markdown,
                todos_raw,
            )
        except BaseException:
            await conn.execute("ROLLBACK")
            raise

        if on_after_commit_before_reply is not None:
            on_after_commit_before_reply(new_version_id)

        # Best-effort post-commit LangGraph checkpoint sync (separate SQLite connection).
        if graph is not None and checkpoint_sync is not None:
            bid, vid, synced_itinerary, synced_req, synced_md, synced_todos = checkpoint_sync
            config = thread_config(bid)
            snapshot = await graph.aget_state(config)
            values = dict(snapshot.values or {})
            patch = {
                "plan_markdown": synced_md,
                "itinerary": synced_itinerary,
                "requirements": synced_req,
                "todos": synced_todos,
                "revision": int(values.get("revision", 0)) + 1,
                "status": values.get("status", PlanStatus.REVIEWING),
                "updated_at": datetime.now(),
            }
            updated = await graph.aupdate_state(config, patch, as_node="refiner")
            new_checkpoint_id = (updated.get("configurable") or {}).get("checkpoint_id")
            if new_checkpoint_id:
                await conn.execute(
                    "UPDATE versions SET checkpoint_id = ? WHERE id = ?",
                    (new_checkpoint_id, vid),
                )
                await conn.commit()

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
                "result_revision_id": new_version_id,
                "travel_id": target["travel_id"],
                "branch_id": target["branch_id"],
                "entity_id": target["entity_id"],
                "origin_direction": "received",
            },
        }
