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


async def find_received_expense_dataset(
    store: SessionStore,
    *,
    session_id: str,
    branch_id: str,
    entity_id: str,
) -> str | None:
    """Return dataset_id if entity_id on (session_id, branch_id) was received from mobile."""
    cur = await store._conn.execute(
        "SELECT dataset_id FROM exchange_operation_receipts "
        "WHERE session_id = ? AND branch_id = ? AND entity_id = ? "
        "AND kind = 'expense.create' AND origin_direction = 'received' "
        "ORDER BY committed_at DESC LIMIT 1",
        (session_id, branch_id, entity_id),
    )
    row = await cur.fetchone()
    return str(row["dataset_id"]) if row is not None else None


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


async def list_outbound_operations(
    store: SessionStore,
    *,
    branch_id: str | None = None,
    entity_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read persisted outbound exchange operations from SQLite."""
    sql = (
        "SELECT dataset_id, source_device_id, operation_id, kind, canonical_profile, "
        "intent_sha256, canonical_intent_json, raw_command_json, expected_revision_id, "
        "source_revision_id, session_id, branch_id, entity_id, delivery_state, "
        "created_at, updated_at FROM exchange_outbound_operations"
    )
    clauses: list[str] = []
    params: list[Any] = []
    if branch_id is not None:
        clauses.append("branch_id = ?")
        params.append(branch_id)
    if entity_id is not None:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at, operation_id"
    cur = await store._conn.execute(sql, params)
    rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def save_expense_patch_with_outbox(
    store: SessionStore,
    graph: Any,
    detail: VersionDetail,
    expense_id: str,
    payload: ExpenseInput,
    *,
    dataset_id: str,
    source_device_id: str = DESKTOP_SOURCE_DEVICE_ID,
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

            next_seq = int(head_row["seq"]) + 1
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
                "source_revision_id, session_id, branch_id, entity_id, delivery_state, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, 'expense.patch', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unsent', ?, ?)",
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
