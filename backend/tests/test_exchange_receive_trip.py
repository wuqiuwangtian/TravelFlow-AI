"""End-to-end Phase 3 tests: receive 0.1.0 offline expense into real trip ledger and read back."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from conftest import FakeLLM, FakeSearch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from test_itinerary_api import generate

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.api import (
    routes_chat,
    routes_export,
    routes_itinerary,
    routes_session,
    routes_studio,
    routes_system,
)
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.services.exchange_receive import (
    ExchangeReceiveScope,
    canonicalize_exchange_intent,
    lookup_exchange_receipt,
    receive_expense_create,
)
from app.storage.database import create_connection
from app.storage.session_store import SessionStore

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMAL_DIGESTS = REPO_ROOT / "contracts" / "expense-create-v0.1.0" / "canonical-digests.json"


def _make_command(
    *,
    travel_id: str,
    branch_id: str,
    expected_revision_id: str,
    operation_id: str = "op-mobile-lunch-1",
    entity_id: str = "mobile-lunch-1",
    amount_cents: int = 8635,
    dataset_id: str = "isolated-trip-dataset",
    source_device_id: str = "mobile-device-1",
) -> dict:
    return {
        "profile": "p1-host-control-1",
        "kind": "expense.create",
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
        "payload": {
            "title": "虚构午餐",
            "amount_cents": amount_cents,
            "currency": "CNY",
            "paid_on": None,
            "category": "meal",
            "day": None,
            "activity_id": None,
            "notes": "",
            "receipt_ids": [],
            "allow_shared_receipts": False,
            "budget_item_id": None,
            "settles_budget": False,
        },
    }


def _make_query(
    *,
    operation_id: str = "op-mobile-lunch-1",
    dataset_id: str = "isolated-trip-dataset",
    source_device_id: str = "mobile-device-1",
) -> dict:
    return {
        "profile": "p1-host-query-1",
        "kind": "receipt.lookup",
        "operation": {
            "dataset_id": dataset_id,
            "source_device_id": source_device_id,
            "operation_id": operation_id,
        },
    }


def _init_sender_db(sender_db: Path, command: dict) -> str:
    raw = json.dumps(command, ensure_ascii=False).encode("utf-8")
    _, canonical, digest = canonicalize_exchange_intent(raw, "control")
    with closing(sqlite3.connect(sender_db)) as db:
        db.execute(
            "CREATE TABLE sender_outbox("
            "operation_id TEXT PRIMARY KEY, intent_sha256 TEXT NOT NULL, "
            "raw_json TEXT NOT NULL, confirmed_revision_id TEXT)"
        )
        db.execute(
            "INSERT INTO sender_outbox VALUES (?, ?, ?, NULL)",
            (command["operation"]["operation_id"], digest, raw.decode("utf-8")),
        )
        db.commit()
    return digest


async def _open_isolated_app(db_path: Path):
    conn = await create_connection(db_path)
    store = SessionStore(conn)
    settings = Settings(_env_file=None, amap_api_key=None)
    checkpointer_cm = open_checkpointer(db_path)
    checkpointer = await checkpointer_cm.__aenter__()
    graph = build_graph(FakeLLM(), FakeSearch(), settings=settings, checkpointer=checkpointer)
    test_app = FastAPI()
    test_app.state.store = store
    test_app.state.graph = graph
    test_app.dependency_overrides[get_settings] = lambda: settings
    register_exception_handlers(test_app)
    for router in (
        routes_session,
        routes_chat,
        routes_itinerary,
        routes_export,
        routes_system,
        routes_studio,
    ):
        test_app.include_router(router.router)
    client = AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")
    return conn, store, checkpointer_cm, graph, client


async def _close_isolated_app(conn, checkpointer_cm, client):
    await client.aclose()
    await checkpointer_cm.__aexit__(None, None, None)
    await conn.close()


def test_canonicalizer_matches_frozen_v010_digests_byte_for_byte():
    doc = json.loads(FORMAL_DIGESTS.read_bytes().decode("utf-8"))
    for case in doc["cases"]:
        _, encoded, digest = canonicalize_exchange_intent(
            case["raw_json"].encode("utf-8"), case["channel"]
        )
        assert encoded.decode("utf-8") == case["canonical_json"]
        assert encoded.hex() == case["canonical_utf8_hex"]
        assert digest == case["sha256"]


async def test_receive_offline_expense_readback_after_reopen_and_rejections(tmp_path):
    trip_db = tmp_path / "desktop-trip.sqlite"
    sender_db = tmp_path / "sender-offline.sqlite"
    scope = ExchangeReceiveScope(
        dataset_id="isolated-trip-dataset",
        authorized_devices=frozenset({"mobile-device-1"}),
    )

    # 1. Create a real desktop trip with main branch + forked branch via existing API.
    conn, store, cp_cm, graph, client = await _open_isolated_app(trip_db)
    try:
        sid, main_bid, initial_detail = await generate(client)
        v1_id = initial_detail["version"]["id"]
        assert initial_detail["itinerary"]["expenses"] == []
        assert initial_detail["itinerary"]["ledger_summary"]["paid_cents"] == 0

        fork_resp = await client.post(
            f"/api/sessions/{sid}/branches",
            json={
                "title": "备选分支",
                "parent_branch_id": main_bid,
                "fork_version_id": v1_id,
            },
        )
        assert fork_resp.status_code == 200
        fork_bid = next(b["id"] for b in fork_resp.json()["items"] if b["id"] != main_bid)
        fork_v1_id = (await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"][0][
            "id"
        ]

        # 2. Independent sender stages a 0.1.0 86.35 CNY expense command in its own SQLite DB.
        cmd = _make_command(travel_id=sid, branch_id=main_bid, expected_revision_id=v1_id)
        expected_sha = _init_sender_db(sender_db, cmd)
        raw_cmd = json.dumps(cmd, ensure_ascii=False).encode("utf-8")

        # 3. Before-commit interruption rolls back completely with zero new versions/receipts.
        def fail_before_commit() -> None:
            raise RuntimeError("simulated crash before COMMIT")

        with pytest.raises(RuntimeError, match="before COMMIT"):
            await receive_expense_create(
                store, raw_cmd, scope=scope, graph=graph, on_before_commit=fail_before_commit
            )
        versions_after_abort = (await client.get(f"/api/branches/{main_bid}/versions")).json()[
            "items"
        ]
        assert [v["id"] for v in versions_after_abort] == [v1_id]
        query_raw = json.dumps(_make_query(), ensure_ascii=False).encode("utf-8")
        looked_before = await lookup_exchange_receipt(store, query_raw, scope=scope)
        assert looked_before == {"ok": True, "state": "not_found_at_settled_read", "receipt": None}

        # 4. Normal receive with reply lost right after atomic COMMIT.
        committed_during_lost_reply: list[str] = []

        def lose_reply(new_vid: str) -> None:
            committed_during_lost_reply.append(new_vid)
            raise RuntimeError("simulated reply lost after COMMIT")

        with pytest.raises(RuntimeError, match="reply lost after COMMIT"):
            await receive_expense_create(
                store,
                raw_cmd,
                scope=scope,
                graph=graph,
                on_after_commit_before_reply=lose_reply,
            )
        assert len(committed_during_lost_reply) == 1
        result_vid = committed_during_lost_reply[0]
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # 5. Reopen the desktop app & DB fresh: read back through existing trip detail / ledger APIs.
    conn, store, cp_cm, graph, client = await _open_isolated_app(trip_db)
    try:
        # Existing branch versions endpoint shows [v1_id, result_vid].
        versions = (await client.get(f"/api/branches/{main_bid}/versions")).json()["items"]
        assert [v["id"] for v in versions] == [v1_id, result_vid]

        # Existing version detail / ledger endpoint shows unique 8635-cent expense.
        detail = (await client.get(f"/api/versions/{result_vid}")).json()
        assert len(detail["itinerary"]["expenses"]) == 1
        exp = detail["itinerary"]["expenses"][0]
        assert exp["id"] == "mobile-lunch-1"
        assert exp["amount_cents"] == 8635
        assert exp["title"] == "虚构午餐"
        assert detail["itinerary"]["ledger_summary"]["paid_cents"] == 8635
        assert detail["itinerary"]["ledger_summary"]["net_expense_cents"] == 8635

        # Existing export endpoint reflects the received expense.
        exported = await client.get(f"/api/versions/{result_vid}/export?format=md")
        assert exported.status_code == 200
        assert "86.35" in exported.text

        # Historical version v1 remains read-only (0 expenses) and fork branch is untouched.
        old_detail = (await client.get(f"/api/versions/{v1_id}")).json()
        assert old_detail["itinerary"]["expenses"] == []
        fork_detail = (await client.get(f"/api/versions/{fork_v1_id}")).json()
        assert fork_detail["itinerary"]["expenses"] == []

        # Sender queries durable receipt by original key and verifies exact intent_sha256.
        query_raw = json.dumps(_make_query(), ensure_ascii=False).encode("utf-8")
        looked = await lookup_exchange_receipt(store, query_raw, scope=scope)
        assert looked["ok"] is True
        assert looked["state"] == "committed"
        assert looked["receipt"]["effect"] == "changed"
        assert looked["receipt"]["result_revision_id"] == result_vid
        assert looked["receipt"]["intent_sha256"] == expected_sha
        assert looked["receipt"]["origin_direction"] == "received"

        # Redelivering the exact same command (with reordered keys) replays result_vid, 0 new rows.
        reordered_raw = json.dumps(cmd, ensure_ascii=False, sort_keys=True).encode("utf-8")
        replayed = await receive_expense_create(store, reordered_raw, scope=scope, graph=graph)
        assert replayed["ok"] is True
        assert replayed["replayed"] is True
        assert replayed["receipt"]["result_revision_id"] == result_vid
        assert len((await client.get(f"/api/branches/{main_bid}/versions")).json()["items"]) == 2

        # Same key with different amount (8635 -> 8636) -> intent_conflict, 0 writes.
        diff_amt = _make_command(
            travel_id=sid, branch_id=main_bid, expected_revision_id=v1_id, amount_cents=8636
        )
        res_conflict = await receive_expense_create(
            store, json.dumps(diff_amt).encode("utf-8"), scope=scope, graph=graph
        )
        assert res_conflict == {"ok": False, "error": "intent_conflict"}

        # Stale baseline (new op + new entity on old v1_id) -> baseline_conflict.
        stale = _make_command(
            travel_id=sid,
            branch_id=main_bid,
            expected_revision_id=v1_id,
            operation_id="op-mobile-stale",
            entity_id="mobile-stale-1",
        )
        res_stale = await receive_expense_create(
            store, json.dumps(stale).encode("utf-8"), scope=scope, graph=graph
        )
        assert res_stale == {
            "ok": False,
            "error": "baseline_conflict",
            "current_revision_id": result_vid,
        }

        # Misdirected to other branch with main's revision -> baseline_conflict, fork untouched.
        wrong_branch = _make_command(
            travel_id=sid,
            branch_id=fork_bid,
            expected_revision_id=v1_id,
            operation_id="op-mobile-wrong-branch",
            entity_id="mobile-wrong-branch-1",
        )
        res_wrong_branch = await receive_expense_create(
            store, json.dumps(wrong_branch).encode("utf-8"), scope=scope, graph=graph
        )
        assert res_wrong_branch == {
            "ok": False,
            "error": "baseline_conflict",
            "current_revision_id": fork_v1_id,
        }

        # Misdirected to non-existent branch -> unauthorized (no receipt leak).
        missing_branch = _make_command(
            travel_id=sid,
            branch_id="no-such-branch",
            expected_revision_id=v1_id,
        )
        res_missing = await receive_expense_create(
            store, json.dumps(missing_branch).encode("utf-8"), scope=scope, graph=graph
        )
        assert res_missing == {"ok": False, "error": "unauthorized"}

        # Final check: main branch has 2 versions and 1 expense (8635); fork untouched.
        final_main = (await client.get(f"/api/versions/{result_vid}")).json()
        assert [(e["id"], e["amount_cents"]) for e in final_main["itinerary"]["expenses"]] == [
            ("mobile-lunch-1", 8635)
        ]
        assert len((await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"]) == 1
    finally:
        await _close_isolated_app(conn, cp_cm, client)
