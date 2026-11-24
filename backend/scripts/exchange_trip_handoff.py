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


def _load_scope(scope_path: Path) -> ExchangeReceiveScope:
    data = json.loads(scope_path.read_bytes().decode("utf-8"))
    targets = frozenset(
        (str(item["travel_id"]), str(item["branch_id"]))
        for item in data.get("authorized_targets", [])
    )
    devices = frozenset(str(dev) for dev in data.get("authorized_devices", []))
    return ExchangeReceiveScope(
        dataset_id=str(data["dataset_id"]),
        authorized_devices=devices,
        authorized_targets=targets,
        can_write=bool(data.get("can_write", True)),
        can_lookup_receipt=bool(data.get("can_lookup_receipt", True)),
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
    work_dir = work_dir.resolve()
    db_path = work_dir / "travel.sqlite"
    scope_path = work_dir / "scope.json"
    if os.path.lexists(db_path) or os.path.lexists(scope_path):
        raise ValueError(
            f"Refusing to overwrite existing handoff files in {work_dir}; choose a new directory."
        )
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(f"{db_path}{suffix}"):
            raise ValueError(f"Refusing to reuse existing SQLite companion file in {work_dir}.")

    expected = json.loads(expected_json.read_bytes().decode("utf-8"))
    raw_sqlite = gzip.decompress(fixture_gz.read_bytes())
    actual_sha256 = hashlib.sha256(raw_sqlite).hexdigest()
    if actual_sha256 != expected["database_sha256"]:
        raise ValueError(
            f"Fixture SHA-256 mismatch: expected {expected['database_sha256']}, got {actual_sha256}"
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, raw_sqlite)
    finally:
        os.close(fd)

    # Ensure exchange_operation_receipts schema exists cleanly on the isolated copy.
    conn = await create_connection(db_path)
    try:
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
    scope_path.write_text(
        json.dumps(scope_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

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
    if context_out is not None:
        context_out.parent.mkdir(parents=True, exist_ok=True)
        context_out.write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return context


async def _run_receive(
    db_path: Path,
    scope_path: Path,
    raw_bytes: bytes,
    *,
    fault: str | None = None,
) -> dict[str, Any]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    scope = _load_scope(scope_path)
    conn = await create_connection(db_path)
    settings = Settings(_env_file=None, amap_api_key=None)
    async with open_checkpointer(db_path) as checkpointer:
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
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    scope = _load_scope(scope_path)
    conn = await create_connection(db_path)
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
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = await create_connection(db_path)
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
