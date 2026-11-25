"""Atomic desktop expense-patch revision + outbound command persistence.

When the desktop user edits an expense that was originally received & confirmed from mobile
(recorded in exchange_operation_receipts with kind='expense.create', origin_direction='received')
and ONLY changes amount_cents, this service atomically commits:
  versions (checkpoint_id=NULL) + plans + sessions.updated_at + exchange_outbound_operations
inside a single exclusive BEGIN IMMEDIATE ... COMMIT transaction before syncing LangGraph.

Multi-field edits or edits to locally created expenses continue through normal _save_revision
and are never mis-emitted as amount-only expense.patch commands.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ConflictError, InvalidRequest
from app.domain.itinerary import ExpenseInput
from app.domain.models import Requirements, VersionDetail
from app.services.exchange_receive import (
    CANONICAL_PROFILE,
    CONTROL_PROFILE,
    QUERY_PROFILE,
    _open_exclusive_connection,
    _resolve_store_db_path,
    recover_exchange_checkpoint,
)
from app.services.itinerary_service import sync_markdown
from app.services.plan_undo import recover_plan_undo
from app.services.travel_service import edit_expense, expense_values
from app.storage.session_store import SessionStore

DESKTOP_SOURCE_DEVICE_ID = "desktop-device-1"
_NON_AMOUNT_EXPENSE_FIELDS = (
    "title",
    "category",
    "day",
    "activity_id",
    "notes",
    "receipt_ids",
    "budget_item_id",
    "settles_budget",
)


def _sort_utf16(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sort_utf16(val)
            for key, val in sorted(value.items(), key=lambda pair: pair[0].encode("utf-16-be"))
        }
    if isinstance(value, list):
        return [_sort_utf16(item) for item in value]
    return value


def build_expense_patch_command(
    *,
    dataset_id: str,
    source_device_id: str,
    operation_id: str,
    travel_id: str,
    branch_id: str,
    entity_id: str,
    expected_revision_id: str,
    source_revision_id: str,
    amount_cents: int,
) -> tuple[bytes, bytes, str]:
    """Build raw command bytes, canonical wrapped bytes, and SHA-256 for expense.patch."""
    command = {
        "profile": CONTROL_PROFILE,
        "kind": "expense.patch",
        "operation": {
            "dataset_id": dataset_id,
            "source_device_id": source_device_id,
            "operation_id": operation_id,
        },
        "target": {
            "travel_id": travel_id,
            "branch_id": branch_id,
            "entity_id": entity_id,
        },
        "expected_revision_id": expected_revision_id,
        "source_revision_id": source_revision_id,
        "patch": {
            "amount_cents": amount_cents,
        },
    }
    raw_bytes = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    wrapped = {
        "canonical_profile": CANONICAL_PROFILE,
        "channel": "control",
        "intent": _sort_utf16(command),
    }
    canonical_bytes = json.dumps(wrapped, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    return raw_bytes, canonical_bytes, digest


async def find_received_expense_origin(
    store: SessionStore,
    *,
    session_id: str,
    branch_id: str,
    entity_id: str,
) -> dict[str, str] | None:
    """Return latest received expense.create identity for (session_id, branch_id, entity_id)."""
    cur = await store._conn.execute(
        "SELECT r.dataset_id, r.source_device_id, r.operation_id "
        "FROM exchange_operation_receipts r "
        "INNER JOIN versions v ON v.id = r.result_version_id "
        "WHERE r.session_id = ? AND r.branch_id = ? AND r.entity_id = ? "
        "  AND r.kind = 'expense.create' AND r.origin_direction = 'received' "
        "ORDER BY v.seq DESC, r.committed_at DESC, r.operation_id DESC LIMIT 1",
        (session_id, branch_id, entity_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "dataset_id": str(row["dataset_id"]),
        "source_device_id": str(row["source_device_id"]),
        "operation_id": str(row["operation_id"]),
    }


async def find_received_expense_dataset(
    store: SessionStore,
    *,
    session_id: str,
    branch_id: str,
    entity_id: str,
) -> str | None:
    """Return dataset_id if entity_id on (session_id, branch_id) was received from mobile."""
    origin = await find_received_expense_origin(
        store, session_id=session_id, branch_id=branch_id, entity_id=entity_id
    )
    return origin["dataset_id"] if origin is not None else None


def is_no_op_expense_edit(detail: VersionDetail, expense_id: str, payload: ExpenseInput) -> bool:
    """True iff expense_id exists and payload changes no persisted expense fields."""
    if detail.itinerary is None:
        return False
    existing = next((e for e in detail.itinerary.expenses if e.id == expense_id), None)
    if existing is None:
        return False
    new_values = expense_values(payload)
    return existing.amount_cents == new_values["amount_cents"] and all(
        getattr(existing, field) == new_values[field] for field in _NON_AMOUNT_EXPENSE_FIELDS
    )


def is_amount_only_expense_edit(
    detail: VersionDetail, expense_id: str, payload: ExpenseInput
) -> bool:
    """True iff expense_id exists and payload changes ONLY amount_cents (non-zero delta)."""
    if detail.itinerary is None:
        return False
    existing = next((e for e in detail.itinerary.expenses if e.id == expense_id), None)
    if existing is None:
        return False
    new_values = expense_values(payload)
    if new_values["amount_cents"] == existing.amount_cents:
        return False
    return all(
        getattr(existing, field) == new_values[field] for field in _NON_AMOUNT_EXPENSE_FIELDS
    )


_OUTBOUND_SELECT = (
    "SELECT dataset_id, source_device_id, operation_id, kind, canonical_profile, "
    "intent_sha256, canonical_intent_json, raw_command_json, expected_revision_id, "
    "source_revision_id, session_id, branch_id, entity_id, "
    "origin_dataset_id, origin_source_device_id, origin_operation_id, delivery_state, "
    "confirmed_mobile_revision_id, confirmed_effect, last_error, "
    "last_raw_response_json, confirmed_at, created_at, updated_at "
    "FROM exchange_outbound_operations"
)


async def resolve_outbound_patch_owner(
    conn: Any,
    outbound_row: Any,
) -> dict[str, str] | None:
    """Resolve the uniquely verified mobile owner of an outbound expense.patch row.

    - Rows with persisted origin_create identity (origin_dataset_id, origin_source_device_id,
      origin_operation_id) are verified against that exact received expense.create receipt.
    - Legacy rows where all three origin columns are NULL are deliverable ONLY when there is
      strictly one matching received expense.create receipt for
      (dataset_id, session_id, branch_id, entity_id); ambiguous (>1) or missing (0) rows
      return None and stay pending.
    - Partially populated origin columns are treated as unverifiable and return None.
    """
    if outbound_row["kind"] != "expense.patch":
        return None

    dataset_id = str(outbound_row["dataset_id"])
    session_id = str(outbound_row["session_id"])
    branch_id = str(outbound_row["branch_id"])
    entity_id = str(outbound_row["entity_id"])

    o_ds = outbound_row["origin_dataset_id"]
    o_dev = outbound_row["origin_source_device_id"]
    o_op = outbound_row["origin_operation_id"]
    origin_parts = (o_ds, o_dev, o_op)

    if any(part is not None for part in origin_parts):
        if not all(isinstance(part, str) and part != "" for part in origin_parts):
            return None
        if o_ds != dataset_id:
            return None
        cur = await conn.execute(
            "SELECT dataset_id, source_device_id, operation_id, session_id, branch_id, entity_id "
            "FROM exchange_operation_receipts "
            "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ? "
            "  AND kind = 'expense.create' AND origin_direction = 'received'",
            (o_ds, o_dev, o_op),
        )
        rec = await cur.fetchone()
        if rec is None:
            return None
        if (
            str(rec["session_id"]) != session_id
            or str(rec["branch_id"]) != branch_id
            or str(rec["entity_id"]) != entity_id
        ):
            return None
        return {
            "dataset_id": str(rec["dataset_id"]),
            "travel_id": str(rec["session_id"]),
            "branch_id": str(rec["branch_id"]),
            "source_device_id": str(rec["source_device_id"]),
            "origin_operation_id": str(rec["operation_id"]),
        }

    # Legacy row without persisted origin binding: allow ONLY when uniquely verifiable.
    cur = await conn.execute(
        "SELECT dataset_id, source_device_id, operation_id, session_id, branch_id, entity_id "
        "FROM exchange_operation_receipts "
        "WHERE dataset_id = ? AND session_id = ? AND branch_id = ? AND entity_id = ? "
        "  AND kind = 'expense.create' AND origin_direction = 'received' "
        "LIMIT 2",
        (dataset_id, session_id, branch_id, entity_id),
    )
    matches = await cur.fetchall()
    if len(matches) != 1:
        return None
    rec = matches[0]
    return {
        "dataset_id": str(rec["dataset_id"]),
        "travel_id": str(rec["session_id"]),
        "branch_id": str(rec["branch_id"]),
        "source_device_id": str(rec["source_device_id"]),
        "origin_operation_id": str(rec["operation_id"]),
    }


async def outbound_patch_matches_grant(
    conn: Any,
    outbound_row: Any,
    grant: dict[str, Any],
) -> bool:
    """Shared ownership check for both reverse-patch pull and receipt confirm."""
    owner = await resolve_outbound_patch_owner(conn, outbound_row)
    if owner is None:
        return False
    return (
        owner["dataset_id"] == str(grant["dataset_id"])
        and owner["travel_id"] == str(grant["travel_id"])
        and owner["branch_id"] == str(grant["branch_id"])
        and owner["source_device_id"] == str(grant["source_device_id"])
    )


def _build_lookup_query_bytes(
    *, dataset_id: str, source_device_id: str, operation_id: str
) -> bytes:
    query = {
        "profile": QUERY_PROFILE,
        "kind": "receipt.lookup",
        "operation": {
            "dataset_id": dataset_id,
            "source_device_id": source_device_id,
            "operation_id": operation_id,
        },
    }
    return json.dumps(query, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def list_outbound_operations(
    store: SessionStore,
    *,
    branch_id: str | None = None,
    entity_id: str | None = None,
    pending_only: bool = False,
) -> list[dict[str, Any]]:
    """Read persisted outbound exchange operations from SQLite."""
    sql = _OUTBOUND_SELECT
    clauses: list[str] = []
    params: list[Any] = []
    if branch_id is not None:
        clauses.append("branch_id = ?")
        params.append(branch_id)
    if entity_id is not None:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if pending_only:
        clauses.append("delivery_state != 'confirmed'")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at, operation_id"
    cur = await store._conn.execute(sql, params)
    rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def prepare_outbound_delivery(
    store: SessionStore,
    *,
    dataset_id: str,
    source_device_id: str,
    operation_id: str,
) -> dict[str, Any]:
    """Durably mark an outbound operation verification_pending before sending."""
    db_path = await _resolve_store_db_path(store)
    tx_conn = (
        await _open_exclusive_connection(db_path, busy_timeout_ms=5000)
        if db_path is not None
        else store._conn
    )
    own_conn = db_path is not None
    try:
        await tx_conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await tx_conn.execute(
                _OUTBOUND_SELECT
                + " WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (dataset_id, source_device_id, operation_id),
            )
            row = await cur.fetchone()
            if row is None:
                await tx_conn.execute("ROLLBACK")
                return {"ok": False, "error": "outbound_not_found"}

            record = dict(row)
            lookup_bytes = _build_lookup_query_bytes(
                dataset_id=dataset_id,
                source_device_id=source_device_id,
                operation_id=operation_id,
            )
            if record["delivery_state"] == "confirmed":
                await tx_conn.execute("ROLLBACK")
                return {
                    "ok": True,
                    "already_confirmed": True,
                    "record": record,
                    "raw_bytes": None,
                    "lookup_query_bytes": lookup_bytes,
                }

            now_utc = datetime.now(UTC).isoformat()
            await tx_conn.execute(
                "UPDATE exchange_outbound_operations "
                "SET delivery_state = 'verification_pending', last_error = 'in_flight', "
                "updated_at = ? "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (now_utc, dataset_id, source_device_id, operation_id),
            )
            cur = await tx_conn.execute(
                _OUTBOUND_SELECT
                + " WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (dataset_id, source_device_id, operation_id),
            )
            updated = dict(await cur.fetchone())  # type: ignore[arg-type]
            await tx_conn.execute("COMMIT")
            return {
                "ok": True,
                "already_confirmed": False,
                "record": updated,
                "raw_bytes": updated["raw_command_json"].encode("utf-8"),
                "lookup_query_bytes": lookup_bytes,
            }
        except BaseException:
            if tx_conn.in_transaction:
                await tx_conn.execute("ROLLBACK")
            raise
    finally:
        if own_conn:
            await tx_conn.close()


def _receipt_matches_outbound(raw_response: dict[str, Any], row: dict[str, Any]) -> bool:
    """Strictly verify operation identity, intent digest, target expense, and desktop version."""
    if raw_response.get("ok") is not True or raw_response.get("state") != "committed":
        return False
    receipt = raw_response.get("receipt")
    if not isinstance(receipt, dict):
        return False
    expected_op = {
        "dataset_id": row["dataset_id"],
        "source_device_id": row["source_device_id"],
        "operation_id": row["operation_id"],
    }
    if receipt.get("operation") != expected_op:
        return False
    if receipt.get("kind") != row["kind"]:
        return False
    if receipt.get("canonical_profile") != row["canonical_profile"]:
        return False
    if receipt.get("intent_sha256") != row["intent_sha256"]:
        return False
    if receipt.get("travel_id") != row["session_id"]:
        return False
    if receipt.get("branch_id") != row["branch_id"]:
        return False
    if receipt.get("entity_id") != row["entity_id"]:
        return False
    if receipt.get("synced_desktop_revision_id") != row["source_revision_id"]:
        return False
    if receipt.get("origin_direction") != "received":
        return False
    expected_effect = (
        "changed" if row["expected_revision_id"] != row["source_revision_id"] else "no_change"
    )
    if receipt.get("effect") != expected_effect:
        return False
    mobile_rev = receipt.get("result_revision_id")
    return isinstance(mobile_rev, str) and 1 <= len(mobile_rev) <= 256


async def consume_outbound_receipt(
    store: SessionStore,
    *,
    dataset_id: str,
    source_device_id: str,
    operation_id: str,
    raw_response: dict[str, Any],
) -> dict[str, Any]:
    """Verify a mobile delivery or lookup response and atomically confirm the outbound row."""
    if not isinstance(raw_response, dict):
        return {"ok": False, "error": "invalid_response", "confirmed": False}

    db_path = await _resolve_store_db_path(store)
    tx_conn = (
        await _open_exclusive_connection(db_path, busy_timeout_ms=5000)
        if db_path is not None
        else store._conn
    )
    own_conn = db_path is not None
    raw_json = json.dumps(raw_response, ensure_ascii=False)
    try:
        await tx_conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await tx_conn.execute(
                _OUTBOUND_SELECT
                + " WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (dataset_id, source_device_id, operation_id),
            )
            row = await cur.fetchone()
            if row is None:
                await tx_conn.execute("ROLLBACK")
                return {"ok": False, "error": "outbound_not_found", "confirmed": False}

            record = dict(row)
            # Never downgrade or rewrite an already confirmed outbound record.
            if record["delivery_state"] == "confirmed":
                await tx_conn.execute("ROLLBACK")
                return {
                    "ok": True,
                    "already_confirmed": True,
                    "confirmed": True,
                    "record": record,
                }

            now_utc = datetime.now(UTC).isoformat()
            if _receipt_matches_outbound(raw_response, record):
                receipt = raw_response["receipt"]
                await tx_conn.execute(
                    "UPDATE exchange_outbound_operations "
                    "SET delivery_state = 'confirmed', confirmed_mobile_revision_id = ?, "
                    "confirmed_effect = ?, last_error = NULL, last_raw_response_json = ?, "
                    "confirmed_at = ?, updated_at = ? "
                    "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                    (
                        receipt["result_revision_id"],
                        receipt["effect"],
                        raw_json,
                        now_utc,
                        now_utc,
                        dataset_id,
                        source_device_id,
                        operation_id,
                    ),
                )
                cur = await tx_conn.execute(
                    _OUTBOUND_SELECT
                    + " WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                    (dataset_id, source_device_id, operation_id),
                )
                updated = dict(await cur.fetchone())  # type: ignore[arg-type]
                await tx_conn.execute("COMMIT")
                return {
                    "ok": True,
                    "already_confirmed": False,
                    "confirmed": True,
                    "record": updated,
                }

            # Mismatched receipt or non-committed response keeps verification_pending.
            if raw_response.get("ok") is True and raw_response.get("state") == "committed":
                err_code = "receipt_mismatch"
            else:
                err_code = str(
                    raw_response.get("error")
                    or raw_response.get("reason")
                    or raw_response.get("state")
                    or "unconfirmed"
                )
            await tx_conn.execute(
                "UPDATE exchange_outbound_operations "
                "SET delivery_state = 'verification_pending', last_error = ?, "
                "last_raw_response_json = ?, updated_at = ? "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (err_code, raw_json, now_utc, dataset_id, source_device_id, operation_id),
            )
            cur = await tx_conn.execute(
                _OUTBOUND_SELECT
                + " WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (dataset_id, source_device_id, operation_id),
            )
            updated = dict(await cur.fetchone())  # type: ignore[arg-type]
            await tx_conn.execute("COMMIT")
            return {
                "ok": False,
                "error": err_code,
                "confirmed": False,
                "record": updated,
            }
        except BaseException:
            if tx_conn.in_transaction:
                await tx_conn.execute("ROLLBACK")
            raise
    finally:
        if own_conn:
            await tx_conn.close()


async def save_expense_patch_with_outbox(
    store: SessionStore,
    graph: Any,
    detail: VersionDetail,
    expense_id: str,
    payload: ExpenseInput,
    *,
    dataset_id: str,
    source_device_id: str = DESKTOP_SOURCE_DEVICE_ID,
    origin_dataset_id: str | None = None,
    origin_source_device_id: str | None = None,
    origin_operation_id: str | None = None,
    on_before_commit: Callable[[], Any] | Callable[[], Awaitable[Any]] | None = None,
) -> VersionDetail:
    """Atomically save a desktop amount-only expense correction and its outbound patch command."""
    branch_id = detail.version.branch_id
    recovered = await recover_plan_undo(store, graph, branch_id)
    if recovered:
        raise ConflictError("上次撤销结果已恢复，请刷新后再保存这次修改。")
    await recover_exchange_checkpoint(store, graph, branch_id)

    if detail.itinerary is None:
        raise InvalidRequest("当前版本没有结构化行程")
    branch = await store.get_branch(branch_id)
    if branch is None:
        raise InvalidRequest("分支不存在")

    # Apply existing domain validation & mutation on an in-memory copy first.
    itinerary = detail.itinerary.model_copy(deep=True)
    edit_expense(itinerary, expense_id, payload)
    requirements = detail.requirements or Requirements()
    new_markdown = sync_markdown(detail.markdown, itinerary, requirements)
    if not new_markdown:
        raise InvalidRequest("路书内容为空，无法保存")

    expected_revision_id = detail.version.id
    new_version_id = uuid.uuid4().hex[:12]
    operation_id = f"op-desktop-patch-{uuid.uuid4().hex[:12]}"
    new_amount_cents = int(payload.amount * 100)

    raw_bytes, canonical_bytes, intent_sha256 = build_expense_patch_command(
        dataset_id=dataset_id,
        source_device_id=source_device_id,
        operation_id=operation_id,
        travel_id=branch.session_id,
        branch_id=branch_id,
        entity_id=expense_id,
        expected_revision_id=expected_revision_id,
        source_revision_id=new_version_id,
        amount_cents=new_amount_cents,
    )

    citations_json = json.dumps(
        [c.model_dump(mode="json") for c in detail.citations], ensure_ascii=False
    )
    todos_json = json.dumps(detail.todos, ensure_ascii=False)
    searched_at = detail.searched_at.isoformat() if detail.searched_at else None
    now_local = datetime.now().isoformat(timespec="seconds")
    now_utc = datetime.now(UTC).isoformat()

    db_path = await _resolve_store_db_path(store)
    tx_conn = (
        await _open_exclusive_connection(db_path, busy_timeout_ms=5000)
        if db_path is not None
        else store._conn
    )
    own_conn = db_path is not None
    try:
        await tx_conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await tx_conn.execute(
                "SELECT id, seq, detail_level FROM versions "
                "WHERE branch_id = ? ORDER BY seq DESC LIMIT 1",
                (branch_id,),
            )
            head_row = await cur.fetchone()
            if head_row is None or str(head_row["id"]) != expected_revision_id:
                await tx_conn.execute("ROLLBACK")
                raise ConflictError("当前查看的是历史版本，请先分叉为新方案再编辑")

            head_seq = int(head_row["seq"])
            bound_origin_ds = origin_dataset_id
            bound_origin_dev = origin_source_device_id
            bound_origin_op = origin_operation_id
            if bound_origin_ds is None or bound_origin_dev is None or bound_origin_op is None:
                orig_cur = await tx_conn.execute(
                    "SELECT r.dataset_id, r.source_device_id, r.operation_id "
                    "FROM exchange_operation_receipts r "
                    "INNER JOIN versions v ON v.id = r.result_version_id "
                    "WHERE r.dataset_id = ? AND r.session_id = ? AND r.branch_id = ? "
                    "  AND r.entity_id = ? AND r.kind = 'expense.create' "
                    "  AND r.origin_direction = 'received' AND v.seq <= ? "
                    "ORDER BY v.seq DESC, r.committed_at DESC, r.operation_id DESC LIMIT 1",
                    (dataset_id, branch.session_id, branch_id, expense_id, head_seq),
                )
                orig_row = await orig_cur.fetchone()
                if orig_row is None:
                    await tx_conn.execute("ROLLBACK")
                    raise InvalidRequest("找不到对应的手机新增回执，无法入队反向更正")
                bound_origin_ds = str(orig_row["dataset_id"])
                bound_origin_dev = str(orig_row["source_device_id"])
                bound_origin_op = str(orig_row["operation_id"])
            else:
                orig_cur = await tx_conn.execute(
                    "SELECT 1 FROM exchange_operation_receipts "
                    "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ? "
                    "  AND session_id = ? AND branch_id = ? AND entity_id = ? "
                    "  AND kind = 'expense.create' AND origin_direction = 'received'",
                    (
                        bound_origin_ds,
                        bound_origin_dev,
                        bound_origin_op,
                        branch.session_id,
                        branch_id,
                        expense_id,
                    ),
                )
                if await orig_cur.fetchone() is None or bound_origin_ds != dataset_id:
                    await tx_conn.execute("ROLLBACK")
                    raise InvalidRequest("原始新增操作身份不匹配，无法入队反向更正")

            next_seq = head_seq + 1
            await tx_conn.execute(
                "INSERT INTO versions(id, branch_id, seq, summary, detail_level, trigger, "
                "checkpoint_id, created_at) VALUES (?, ?, ?, ?, ?, 'itinerary_edit', NULL, ?)",
                (
                    new_version_id,
                    branch_id,
                    next_seq,
                    f"更正消费：{payload.title}",
                    head_row["detail_level"],
                    now_local,
                ),
            )
            await tx_conn.execute(
                "INSERT INTO plans(version_id, markdown, citations, todos, searched_at, "
                "itinerary, requirements) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_version_id,
                    new_markdown,
                    citations_json,
                    todos_json,
                    searched_at,
                    itinerary.model_dump_json(),
                    requirements.model_dump_json(),
                ),
            )
            await tx_conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now_local, branch.session_id),
            )
            await tx_conn.execute(
                "INSERT INTO exchange_outbound_operations("
                "dataset_id, source_device_id, operation_id, kind, canonical_profile, "
                "intent_sha256, canonical_intent_json, raw_command_json, expected_revision_id, "
                "source_revision_id, session_id, branch_id, entity_id, "
                "origin_dataset_id, origin_source_device_id, origin_operation_id, "
                "delivery_state, created_at, updated_at"
                ") VALUES (?, ?, ?, 'expense.patch', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'unsent', ?, ?)",
                (
                    dataset_id,
                    source_device_id,
                    operation_id,
                    CANONICAL_PROFILE,
                    intent_sha256,
                    canonical_bytes.decode("utf-8"),
                    raw_bytes.decode("utf-8"),
                    expected_revision_id,
                    new_version_id,
                    branch.session_id,
                    branch_id,
                    expense_id,
                    bound_origin_ds,
                    bound_origin_dev,
                    bound_origin_op,
                    now_utc,
                    now_utc,
                ),
            )
            if on_before_commit is not None:
                res = on_before_commit()
                if inspect.isawaitable(res):
                    await res
            await tx_conn.execute("COMMIT")
        except BaseException:
            if tx_conn.in_transaction:
                await tx_conn.execute("ROLLBACK")
            raise
    finally:
        if own_conn:
            await tx_conn.close()

    await recover_exchange_checkpoint(store, graph, branch_id)
    saved = await store.get_version_detail(new_version_id)
    assert saved is not None
    return saved
