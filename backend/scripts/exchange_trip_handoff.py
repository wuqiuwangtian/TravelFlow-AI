"""Maintainer CLI for isolated cross-repo 0.1.0 expense handoff on a fictional trip copy.

Usage (from repo root, using backend venv):
  backend/.venv/bin/python backend/scripts/exchange_trip_handoff.py init-trip --work-dir <new-dir>
  backend/.venv/bin/python backend/scripts/exchange_trip_handoff.py receive \
      --database <work-dir>/travel.sqlite --scope <work-dir>/scope.json --input <cmd.json | ->
  backend/.venv/bin/python backend/scripts/exchange_trip_handoff.py lookup \
      --database <work-dir>/travel.sqlite --scope <work-dir>/scope.json --input <query.json | ->
  backend/.venv/bin/python backend/scripts/exchange_trip_handoff.py inspect-trip \
      --database <work-dir>/travel.sqlite --branch-id <branch_id>

Boundaries:
- Operates ONLY on newly created isolated copies of the D1 fictional trip fixture.
- Never opens personal databases, never reads .env, never starts listening sockets, never modifies
  contracts/, and never references adjacent mobile repository paths.
- Keeps stdout strictly reserved for raw JSON output (or empty on simulated process-crash faults).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Ensure app logs never pollute stdout (stdout is reserved for raw JSON receipts/context).
logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.agents.checkpoint import open_checkpointer  # noqa: E402
from app.agents.graph import build_graph  # noqa: E402
from app.api import (  # noqa: E402
    routes_export,
    routes_itinerary,
    routes_session,
)
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.errors import register_exception_handlers  # noqa: E402
from app.services.exchange_outbox import (  # noqa: E402
    consume_outbound_receipt,
    list_outbound_operations,
    prepare_outbound_delivery,
)
from app.services.exchange_receive import (  # noqa: E402
    CONTRACT_ID,
    CONTRACT_VERSION,
    ExchangeReceiveScope,
    lookup_exchange_receipt,
    receive_expense_create,
)
from app.storage.database import create_connection  # noqa: E402
from app.storage.session_store import SessionStore  # noqa: E402

HANDOFF_FORMAT = "travelflow-p3-trip-handoff/0-draft"
HANDOFF_APPLICATION_ID = 0x54465033  # 'TFP3'
HANDOFF_REQUIRED_TABLES = frozenset(
    {
        "handoff_trip_meta",
        "sessions",
        "branches",
        "versions",
        "plans",
        "exchange_operation_receipts",
    }
)
SCOPE_REQUIRED_KEYS = frozenset(
    {
        "dataset_id",
        "authorized_devices",
        "authorized_targets",
        "can_write",
        "can_lookup_receipt",
    }
)
DEFAULT_FIXTURE_GZ = (
    REPO_ROOT / "desktop" / "tests" / "fixtures" / "d1" / "fictional-trip.sqlite.gz"
)
DEFAULT_EXPECTED_JSON = REPO_ROOT / "desktop" / "tests" / "fixtures" / "d1" / "expected.json"
DEFAULT_DATASET_ID = "p3-d1-handoff-dataset"
DEFAULT_DEVICE_ID = "mobile-handoff-device-1"


class _OfflineNoopLLM:
    """Placeholder LLM for offline checkpoint graph construction (never invoked on receive)."""

    name = "offline-noop"

    async def structured(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Offline handoff CLI does not invoke external LLMs.")

    async def chat(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("Offline handoff CLI does not invoke external LLMs.")

    async def stream(self, *args: Any, **kwargs: Any):
        raise RuntimeError("Offline handoff CLI does not invoke external LLMs.")
        yield ""  # pragma: no cover


class _OfflineNoopSearch:
    """Placeholder search provider for offline graph construction (never invoked on receive)."""

    async def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("Offline handoff CLI does not invoke external search.")


def _read_raw_input(input_arg: str) -> bytes:
    if input_arg == "-":
        return sys.stdin.buffer.read()
    path = Path(input_arg)
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")
    return path.read_bytes()


def _write_exclusive_bytes(path: Path, content: bytes) -> None:
    """Create a new regular file exclusively without following symlinks or overwriting."""
    if os.path.lexists(path):
        raise ValueError(f"Refusing to overwrite existing path: {path}")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate_handoff_database(db_path: Path) -> Path:
    """Read-only verification that db_path is an isolated handoff copy created by init-trip."""
    if db_path.is_symlink() or not db_path.is_file():
        raise ValueError("invalid_handoff_database")
    resolved = db_path.resolve(strict=True)
    # Opening even a read-only SQLite connection can create WAL/SHM files.
    # init-trip stamps and closes this database before handing it to callers.
    with resolved.open("rb") as stream:
        stream.seek(68)  # SQLite database header: four-byte big-endian application_id.
        if stream.read(4) != HANDOFF_APPLICATION_ID.to_bytes(4, "big"):
            raise ValueError("invalid_handoff_database")
    try:
        with closing(sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=5.0)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            if app_id != HANDOFF_APPLICATION_ID:
                raise ValueError("invalid_handoff_database")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            }
            if not tables >= HANDOFF_REQUIRED_TABLES:
                raise ValueError("invalid_handoff_database")
            meta = db.execute(
                "SELECT handoff_format, fixture_database_sha256 FROM handoff_trip_meta WHERE id = 1"
            ).fetchone()
            if (
                meta is None
                or meta["handoff_format"] != HANDOFF_FORMAT
                or not isinstance(meta["fixture_database_sha256"], str)
                or len(meta["fixture_database_sha256"]) != 64
            ):
                raise ValueError("invalid_handoff_database")
    except sqlite3.Error:
        raise ValueError("invalid_handoff_database") from None
    return resolved


def _load_scope(scope_path: Path) -> ExchangeReceiveScope:
    """Strictly parse explicit test authorization config; fail closed on missing/malformed keys."""
    if scope_path.is_symlink() or not scope_path.is_file():
        raise ValueError("invalid_scope_config")
    try:
        data = json.loads(scope_path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid_scope_config") from None

    if not isinstance(data, dict) or set(data.keys()) != SCOPE_REQUIRED_KEYS:
        raise ValueError("invalid_scope_config")

    dataset_id = data["dataset_id"]
    if not isinstance(dataset_id, str) or not (1 <= len(dataset_id) <= 256):
        raise ValueError("invalid_scope_config")

    raw_devices = data["authorized_devices"]
    if not isinstance(raw_devices, list) or not all(
        isinstance(dev, str) and 1 <= len(dev) <= 256 for dev in raw_devices
    ):
        raise ValueError("invalid_scope_config")

    raw_targets = data["authorized_targets"]
    if not isinstance(raw_targets, list):
        raise ValueError("invalid_scope_config")
    parsed_targets: list[tuple[str, str]] = []
    for item in raw_targets:
        if not isinstance(item, dict) or set(item.keys()) != {"travel_id", "branch_id"}:
            raise ValueError("invalid_scope_config")
        travel_id = item["travel_id"]
        branch_id = item["branch_id"]
        if (
            not isinstance(travel_id, str)
            or not (1 <= len(travel_id) <= 256)
            or not isinstance(branch_id, str)
            or not (1 <= len(branch_id) <= 256)
        ):
            raise ValueError("invalid_scope_config")
        parsed_targets.append((travel_id, branch_id))

    can_write = data["can_write"]
    can_lookup_receipt = data["can_lookup_receipt"]
    if type(can_write) is not bool or type(can_lookup_receipt) is not bool:
        raise ValueError("invalid_scope_config")

    return ExchangeReceiveScope(
        dataset_id=dataset_id,
        authorized_devices=frozenset(raw_devices),
        authorized_targets=frozenset(parsed_targets),
        can_write=can_write,
        can_lookup_receipt=can_lookup_receipt,
    )


async def _init_trip(
    work_dir: Path,
    *,
    dataset_id: str,
    device_id: str,
    fixture_gz: Path = DEFAULT_FIXTURE_GZ,
    expected_json: Path = DEFAULT_EXPECTED_JSON,
    context_out: Path | None = None,
) -> dict[str, Any]:
    if os.path.islink(work_dir):
        raise ValueError(f"Refusing to use symlink work directory: {work_dir}")
    resolved_work_dir = work_dir.resolve()
    db_path = resolved_work_dir / "travel.sqlite"
    scope_path = resolved_work_dir / "scope.json"
    reserved_paths = {
        resolved_work_dir,
        db_path,
        scope_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-journal"),
    }

    if os.path.lexists(db_path) or os.path.lexists(scope_path):
        raise ValueError(
            f"Refusing to overwrite existing handoff files in {resolved_work_dir}; "
            "choose a new directory."
        )
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(f"{db_path}{suffix}"):
            raise ValueError(
                f"Refusing to reuse existing SQLite companion file in {resolved_work_dir}."
            )

    resolved_context_out: Path | None = None
    if context_out is not None:
        if os.path.lexists(context_out):
            raise ValueError(f"Refusing to overwrite existing context output path: {context_out}")
        resolved_context_out = context_out.parent.resolve() / context_out.name
        if os.path.lexists(resolved_context_out) or resolved_context_out in reserved_paths:
            raise ValueError(
                f"Context output path conflicts with existing or reserved path: {context_out}"
            )

    expected = json.loads(expected_json.read_bytes().decode("utf-8"))
    raw_sqlite = gzip.decompress(fixture_gz.read_bytes())
    actual_sha256 = hashlib.sha256(raw_sqlite).hexdigest()
    if actual_sha256 != expected["database_sha256"]:
        raise ValueError(
            f"Fixture SHA-256 mismatch: expected {expected['database_sha256']}, got {actual_sha256}"
        )

    resolved_work_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[Path] = []
    try:
        _write_exclusive_bytes(db_path, raw_sqlite)
        created_files.append(db_path)

        # Ensure exchange_operation_receipts schema exists and stamp isolated handoff metadata.
        conn = await create_connection(db_path)
        try:
            await conn.execute(f"PRAGMA application_id = {HANDOFF_APPLICATION_ID}")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS handoff_trip_meta ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "handoff_format TEXT NOT NULL, "
                "fixture_database_sha256 TEXT NOT NULL, "
                "dataset_id TEXT NOT NULL, "
                "travel_id TEXT NOT NULL, "
                "branch_id TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            await conn.execute(
                "INSERT INTO handoff_trip_meta("
                "id, handoff_format, fixture_database_sha256, dataset_id, travel_id, "
                "branch_id, created_at"
                ") VALUES (1, ?, ?, ?, ?, ?, ?)",
                (
                    HANDOFF_FORMAT,
                    actual_sha256,
                    dataset_id,
                    expected["session"],
                    expected["main_branch"],
                    datetime.now(UTC).isoformat(),
                ),
            )
            await conn.commit()

            store = SessionStore(conn)
            versions = await store.list_versions(expected["main_branch"])
            fork_versions = await store.list_versions(expected["fork_branch"])
            if not versions or not fork_versions:
                raise ValueError("Fixture database is missing expected versions.")
            current_revision_id = versions[-1].id
            fork_revision_id = fork_versions[-1].id
        finally:
            await conn.close()

        scope_doc = {
            "dataset_id": dataset_id,
            "authorized_devices": [device_id],
            "authorized_targets": [
                {
                    "travel_id": expected["session"],
                    "branch_id": expected["main_branch"],
                }
            ],
            "can_write": True,
            "can_lookup_receipt": True,
        }
        _write_exclusive_bytes(
            scope_path,
            (json.dumps(scope_doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        created_files.append(scope_path)

        context = {
            "handoff_format": HANDOFF_FORMAT,
            "contract_id": CONTRACT_ID,
            "contract_version": CONTRACT_VERSION,
            "fixture_source": str(fixture_gz.relative_to(REPO_ROOT)),
            "fixture_database_sha256": actual_sha256,
            "database": str(db_path),
            "scope_config": str(scope_path),
            "dataset_id": dataset_id,
            "authorized_devices": [device_id],
            "travel_id": expected["session"],
            "branch_id": expected["main_branch"],
            "current_revision_id": current_revision_id,
            "fork_branch_id": expected["fork_branch"],
            "fork_revision_id": fork_revision_id,
        }
        if resolved_context_out is not None:
            resolved_context_out.parent.mkdir(parents=True, exist_ok=True)
            _write_exclusive_bytes(
                resolved_context_out,
                (json.dumps(context, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            created_files.append(resolved_context_out)
        return context
    except BaseException:
        if db_path in created_files:
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        for created in reversed(created_files):
            created.unlink(missing_ok=True)
        raise


async def _run_receive(
    db_path: Path,
    scope_path: Path,
    raw_bytes: bytes,
    *,
    fault: str | None = None,
) -> dict[str, Any]:
    resolved_db = _validate_handoff_database(db_path)
    scope = _load_scope(scope_path)
    conn = await create_connection(resolved_db)
    settings = Settings(_env_file=None, amap_api_key=None)
    async with open_checkpointer(resolved_db) as checkpointer:
        graph = build_graph(
            _OfflineNoopLLM(),  # type: ignore[arg-type]
            _OfflineNoopSearch(),  # type: ignore[arg-type]
            settings=settings,
            checkpointer=checkpointer,
        )
        store = SessionStore(conn)

        def _before_commit_fault() -> None:
            os._exit(85)

        def _after_commit_fault(_version_id: str) -> None:
            os._exit(86)

        try:
            return await receive_expense_create(
                store,
                raw_bytes,
                scope=scope,
                graph=graph,
                on_before_commit=_before_commit_fault if fault == "before_commit" else None,
                on_after_commit_before_reply=(
                    _after_commit_fault if fault == "after_commit_before_reply" else None
                ),
            )
        finally:
            await conn.close()


async def _run_lookup(db_path: Path, scope_path: Path, raw_bytes: bytes) -> dict[str, Any]:
    resolved_db = _validate_handoff_database(db_path)
    scope = _load_scope(scope_path)
    conn = await create_connection(resolved_db)
    try:
        store = SessionStore(conn)
        return await lookup_exchange_receipt(store, raw_bytes, scope=scope)
    finally:
        await conn.close()


async def _inspect_trip(
    db_path: Path,
    *,
    branch_id: str,
    fork_branch_id: str | None = None,
) -> dict[str, Any]:
    """Read real trip ledger through existing FastAPI endpoints (no listening socket)."""
    resolved_db = _validate_handoff_database(db_path)
    conn = await create_connection(resolved_db)
    settings = Settings(_env_file=None, amap_api_key=None)
    store = SessionStore(conn)
    test_app = FastAPI()
    test_app.state.store = store
    test_app.dependency_overrides[get_settings] = lambda: settings
    register_exception_handlers(test_app)
    for router in (routes_session, routes_itinerary, routes_export):
        test_app.include_router(router.router)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://handoff.local"
        ) as client:
            versions_res = await client.get(f"/api/branches/{branch_id}/versions")
            if versions_res.status_code != 200:
                raise RuntimeError(
                    f"GET /api/branches/{branch_id}/versions failed: {versions_res.status_code}"
                )
            version_items = versions_res.json()["items"]
            version_ids = [item["id"] for item in version_items]
            head_id = version_ids[-1]

            head_detail = (await client.get(f"/api/versions/{head_id}")).json()
            head_expenses = head_detail["itinerary"]["expenses"]
            export_res = await client.get(f"/api/versions/{head_id}/export")
            if export_res.status_code != 200:
                raise RuntimeError(
                    f"GET /api/versions/{head_id}/export failed: {export_res.status_code}"
                )

            previous_expenses = None
            if len(version_ids) >= 2:
                prev_detail = (await client.get(f"/api/versions/{version_ids[-2]}")).json()
                previous_expenses = prev_detail["itinerary"]["expenses"]

            fork_summary = None
            if fork_branch_id:
                fork_items = (await client.get(f"/api/branches/{fork_branch_id}/versions")).json()[
                    "items"
                ]
                fork_head_id = fork_items[-1]["id"]
                fork_detail = (await client.get(f"/api/versions/{fork_head_id}")).json()
                fork_summary = {
                    "branch_id": fork_branch_id,
                    "version_ids": [item["id"] for item in fork_items],
                    "head_expense_count": len(fork_detail["itinerary"]["expenses"]),
                }
    finally:
        await conn.close()

    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        receipts = [
            {
                "dataset_id": row["dataset_id"],
                "source_device_id": row["source_device_id"],
                "operation_id": row["operation_id"],
                "intent_sha256": row["intent_sha256"],
                "result_version_id": row["result_version_id"],
                "entity_id": row["entity_id"],
                "origin_direction": row["origin_direction"],
            }
            for row in db.execute(
                "SELECT dataset_id, source_device_id, operation_id, intent_sha256, "
                "result_version_id, entity_id, origin_direction "
                "FROM exchange_operation_receipts ORDER BY committed_at, operation_id"
            ).fetchall()
        ]
        outbound_rows = [
            dict(row)
            for row in db.execute(
                "SELECT dataset_id, source_device_id, operation_id, kind, intent_sha256, "
                "expected_revision_id, source_revision_id, entity_id, "
                "origin_dataset_id, origin_source_device_id, origin_operation_id, "
                "delivery_state, confirmed_mobile_revision_id, confirmed_effect, last_error "
                "FROM exchange_outbound_operations ORDER BY created_at, operation_id"
            ).fetchall()
        ]

    return {
        "branch_id": branch_id,
        "version_ids": version_ids,
        "head_revision_id": head_id,
        "head_checkpoint_id": head_detail["version"]["checkpoint_id"],
        "head_expenses": [
            {
                "id": e["id"],
                "title": e["title"],
                "amount_cents": e["amount_cents"],
                "category": e["category"],
            }
            for e in head_expenses
        ],
        "head_paid_cents": sum(int(e["amount_cents"]) for e in head_expenses),
        "previous_expense_count": (
            len(previous_expenses) if previous_expenses is not None else None
        ),
        "export_markdown": export_res.text,
        "fork": fork_summary,
        "receipts": receipts,
        "outbound_operations": outbound_rows,
        "pending_outbound_count": sum(
            1 for row in outbound_rows if row["delivery_state"] != "confirmed"
        ),
    }


async def _prepare_outbound(
    db_path: Path,
    *,
    operation_id: str | None = None,
    command_out: Path | None = None,
    query_out: Path | None = None,
) -> dict[str, Any]:
    """Durably mark an outbound patch verification_pending and return/write raw command bytes."""
    resolved_db = _validate_handoff_database(db_path)
    reserved = {Path(f"{resolved_db}{suffix}") for suffix in ("", "-wal", "-shm", "-journal")}
    for output in (command_out, query_out):
        if output is None:
            continue
        resolved = output.resolve()
        if os.path.lexists(output) or resolved in reserved:
            raise ValueError(f"Output path conflicts with an existing or reserved path: {output}")
        reserved.add(resolved)

    conn = await create_connection(resolved_db)
    try:
        store = SessionStore(conn)
        rows = await list_outbound_operations(store)
        if operation_id is not None:
            matches = [r for r in rows if r["operation_id"] == operation_id]
        else:
            matches = [r for r in rows if r["delivery_state"] != "confirmed"]
        if len(matches) != 1:
            return {
                "ok": False,
                "error": "outbound_not_found" if not matches else "ambiguous_outbound_operation",
            }
        target = matches[0]
        res = await prepare_outbound_delivery(
            store,
            dataset_id=target["dataset_id"],
            source_device_id=target["source_device_id"],
            operation_id=target["operation_id"],
        )
    finally:
        await conn.close()

    if not res.get("ok"):
        return res

    raw_bytes: bytes | None = res.pop("raw_bytes")
    lookup_bytes: bytes = res.pop("lookup_query_bytes")
    if command_out is not None and raw_bytes is not None:
        command_out.parent.mkdir(parents=True, exist_ok=True)
        _write_exclusive_bytes(command_out, raw_bytes)
    if query_out is not None:
        query_out.parent.mkdir(parents=True, exist_ok=True)
        _write_exclusive_bytes(query_out, lookup_bytes)

    res["raw_command_json"] = raw_bytes.decode("utf-8") if raw_bytes is not None else None
    res["lookup_query_json"] = lookup_bytes.decode("utf-8")
    return res


async def _confirm_outbound(
    db_path: Path,
    *,
    operation_id: str,
    raw_response_bytes: bytes,
) -> dict[str, Any]:
    """Verify a raw mobile receipt/lookup response and atomically confirm the outbound row."""
    resolved_db = _validate_handoff_database(db_path)
    try:
        raw_response = json.loads(raw_response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid_response", "confirmed": False}
    if not isinstance(raw_response, dict):
        return {"ok": False, "error": "invalid_response", "confirmed": False}

    conn = await create_connection(resolved_db)
    try:
        store = SessionStore(conn)
        rows = await list_outbound_operations(store)
        matches = [r for r in rows if r["operation_id"] == operation_id]
        if len(matches) != 1:
            return {"ok": False, "error": "outbound_not_found", "confirmed": False}
        target = matches[0]
        return await consume_outbound_receipt(
            store,
            dataset_id=target["dataset_id"],
            source_device_id=target["source_device_id"],
            operation_id=target["operation_id"],
            raw_response=raw_response,
        )
    finally:
        await conn.close()


async def _outbound_status(db_path: Path, *, branch_id: str | None = None) -> dict[str, Any]:
    """Read persisted outbound operations (pending vs confirmed) on an isolated handoff DB."""
    resolved_db = _validate_handoff_database(db_path)
    conn = await create_connection(resolved_db)
    try:
        store = SessionStore(conn)
        all_rows = await list_outbound_operations(store, branch_id=branch_id)
    finally:
        await conn.close()
    pending = [r for r in all_rows if r["delivery_state"] != "confirmed"]
    confirmed = [r for r in all_rows if r["delivery_state"] == "confirmed"]
    return {
        "ok": True,
        "all": all_rows,
        "pending": pending,
        "confirmed": confirmed,
        "pending_count": len(pending),
        "confirmed_count": len(confirmed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Isolated cross-repo 0.1.0 expense handoff CLI for fictional desktop trips."
    )
    sub = parser.add_subparsers(dest="action", required=True)

    init_p = sub.add_parser("init-trip", help="Create a new isolated D1 fictional trip copy.")
    init_p.add_argument("--work-dir", type=Path, required=True)
    init_p.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    init_p.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    init_p.add_argument("--context-out", type=Path, default=None)

    recv_p = sub.add_parser(
        "receive", help="Deliver raw 0.1.0 command bytes from file or stdin and print raw receipt."
    )
    recv_p.add_argument("--database", type=Path, required=True)
    recv_p.add_argument("--scope", type=Path, required=True)
    recv_p.add_argument(
        "--input", default="-", help="Path to raw command JSON bytes, or '-' stdin."
    )
    recv_p.add_argument(
        "--fault",
        choices=("before_commit", "after_commit_before_reply"),
        default=None,
    )

    look_p = sub.add_parser(
        "lookup", help="Deliver raw 0.1.0 receipt.lookup bytes from file or stdin."
    )
    look_p.add_argument("--database", type=Path, required=True)
    look_p.add_argument("--scope", type=Path, required=True)
    look_p.add_argument("--input", default="-", help="Path to raw query JSON bytes, or '-' stdin.")

    insp_p = sub.add_parser(
        "inspect-trip", help="Read desktop trip ledger via existing FastAPI endpoints."
    )
    insp_p.add_argument("--database", type=Path, required=True)
    insp_p.add_argument("--branch-id", required=True)
    insp_p.add_argument("--fork-branch-id", default=None)

    prep_p = sub.add_parser(
        "prepare-outbound",
        help="Mark an outbound patch verification_pending and emit its raw command & lookup JSON.",
    )
    prep_p.add_argument("--database", type=Path, required=True)
    prep_p.add_argument("--operation-id", default=None)
    prep_p.add_argument("--command-out", type=Path, default=None)
    prep_p.add_argument("--query-out", type=Path, default=None)

    conf_p = sub.add_parser(
        "confirm-outbound",
        help="Verify mobile delivery/lookup response and atomically confirm the outbound row.",
    )
    conf_p.add_argument("--database", type=Path, required=True)
    conf_p.add_argument("--operation-id", required=True)
    conf_p.add_argument(
        "--input", default="-", help="Path to mobile raw JSON response, or '-' stdin."
    )

    stat_p = sub.add_parser(
        "outbound-status",
        help="Read pending and confirmed outbound patch operations from an isolated handoff DB.",
    )
    stat_p.add_argument("--database", type=Path, required=True)
    stat_p.add_argument("--branch-id", default=None)

    args = parser.parse_args(argv)
    try:
        if args.action == "init-trip":
            result = asyncio.run(
                _init_trip(
                    args.work_dir,
                    dataset_id=args.dataset_id,
                    device_id=args.device_id,
                    context_out=args.context_out,
                )
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0

        if args.action == "receive":
            raw_bytes = _read_raw_input(args.input)
            result = asyncio.run(
                _run_receive(args.database, args.scope, raw_bytes, fault=args.fault)
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 1

        if args.action == "lookup":
            raw_bytes = _read_raw_input(args.input)
            result = asyncio.run(_run_lookup(args.database, args.scope, raw_bytes))
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 1

        if args.action == "prepare-outbound":
            result = asyncio.run(
                _prepare_outbound(
                    args.database,
                    operation_id=args.operation_id,
                    command_out=args.command_out,
                    query_out=args.query_out,
                )
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 1

        if args.action == "confirm-outbound":
            raw_bytes = _read_raw_input(args.input)
            result = asyncio.run(
                _confirm_outbound(
                    args.database,
                    operation_id=args.operation_id,
                    raw_response_bytes=raw_bytes,
                )
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 1

        if args.action == "outbound-status":
            result = asyncio.run(_outbound_status(args.database, branch_id=args.branch_id))
            print(json.dumps(result, ensure_ascii=False))
            return 0

        result = asyncio.run(
            _inspect_trip(
                args.database,
                branch_id=args.branch_id,
                fork_branch_id=args.fork_branch_id,
            )
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
