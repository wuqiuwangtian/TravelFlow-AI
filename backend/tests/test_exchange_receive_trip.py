"""End-to-end Phase 3 tests: receive 0.1.0 offline expense into real trip ledger and read back."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from conftest import FakeLLM, FakeSearch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from test_itinerary_api import generate

from app.agents.checkpoint import open_checkpointer, thread_config
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from desktop.expense_pending import (  # noqa: E402
    consume_delivery_response,
    consume_lookup_response,
    init_sender_store,
    prepare_pending_delivery,
    read_pending_operation,
    stage_pending_operation,
)

FORMAL_DIGESTS = REPO_ROOT / "contracts" / "expense-create-v0.1.0" / "canonical-digests.json"


def _make_command(
    *,
    travel_id: str,
    branch_id: str,
    expected_revision_id: str,
    operation_id: str = "op-mobile-lunch-1",
    entity_id: str = "mobile-lunch-1",
    title: str = "虚构午餐",
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
            "title": title,
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


async def _generate_trip(client: AsyncClient) -> tuple[str, str, str]:
    sid, bid, detail = await generate(client)
    return sid, bid, str(detail["version"]["id"])


async def _deliver_pending_to_desktop(
    sender_db: Path,
    store: SessionStore,
    graph,
    *,
    scope: ExchangeReceiveScope,
    source_device_id: str,
    operation_id: str,
    on_after_commit_before_reply=None,
) -> dict:
    """Isolated transport adapter: deliver persisted pending record and consume receipt."""
    prep = prepare_pending_delivery(
        sender_db,
        dataset_id=scope.dataset_id,
        source_device_id=source_device_id,
        operation_id=operation_id,
    )
    if not prep["ok"] or prep["already_confirmed"]:
        return prep

    try:
        receiver_raw = await receive_expense_create(
            store,
            prep["raw_bytes"],
            scope=scope,
            graph=graph,
            on_after_commit_before_reply=on_after_commit_before_reply,
        )
    except ConnectionResetError:
        return consume_delivery_response(
            sender_db,
            dataset_id=scope.dataset_id,
            source_device_id=source_device_id,
            operation_id=operation_id,
            receiver_raw={"transport_error": "reply_lost"},
            transport_error="reply_lost",
        )

    return consume_delivery_response(
        sender_db,
        dataset_id=scope.dataset_id,
        source_device_id=source_device_id,
        operation_id=operation_id,
        receiver_raw=receiver_raw,
    )


async def _reconcile_pending_with_desktop(
    sender_db: Path,
    store: SessionStore,
    *,
    scope: ExchangeReceiveScope,
    source_device_id: str,
    operation_id: str,
) -> dict:
    query = _make_query(
        operation_id=operation_id,
        dataset_id=scope.dataset_id,
        source_device_id=source_device_id,
    )
    receiver_raw = await lookup_exchange_receipt(
        store,
        json.dumps(query, ensure_ascii=False).encode("utf-8"),
        scope=scope,
    )
    return consume_lookup_response(
        sender_db,
        dataset_id=scope.dataset_id,
        source_device_id=source_device_id,
        operation_id=operation_id,
        receiver_raw=receiver_raw,
    )


def test_canonicalizer_matches_frozen_v010_digests_byte_for_byte():
    doc = json.loads(FORMAL_DIGESTS.read_bytes().decode("utf-8"))
    for case in doc["cases"]:
        _, encoded, digest = canonicalize_exchange_intent(
            case["raw_json"].encode("utf-8"), case["channel"]
        )
        assert encoded.decode("utf-8") == case["canonical_json"]
        assert encoded.hex() == case["canonical_utf8_hex"]
        assert digest == case["sha256"]


@pytest.mark.asyncio
async def test_exclusive_transaction_prevents_shared_connection_half_commit(tmp_path: Path):
    """Concurrent touch_session on shared SessionStore must not half-commit or be rolled back."""
    app_db = tmp_path / "desktop_concurrent.db"
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        sid, bid, v1 = await _generate_trip(client)
        other_session = await store.create_session("并发隔离测试会话")
        await conn.execute(
            "UPDATE sessions SET updated_at = '2025-01-01T00:00:00' WHERE id = ?",
            (other_session.id,),
        )
        await conn.commit()

        scope = ExchangeReceiveScope(
            dataset_id="isolated-trip-dataset",
            authorized_devices=frozenset({"mobile-device-1"}),
            authorized_targets=frozenset({(sid, bid)}),
        )
        cmd_bytes = json.dumps(
            _make_command(travel_id=sid, branch_id=bid, expected_revision_id=v1),
            ensure_ascii=False,
        ).encode("utf-8")

        inserted = asyncio.Event()
        resume = asyncio.Event()

        async def _pause_after_version_insert():
            inserted.set()
            await resume.wait()

        def _abort_before_commit():
            raise RuntimeError("injected failure before commit")

        receive_task = asyncio.create_task(
            receive_expense_create(
                store,
                cmd_bytes,
                scope=scope,
                graph=graph,
                on_after_version_insert=_pause_after_version_insert,
                on_before_commit=_abort_before_commit,
            )
        )
        await asyncio.wait_for(inserted.wait(), timeout=5.0)

        # Run a normal SessionStore write on the shared connection while receive holds its
        # exclusive transaction lock, then resume receive to abort and rollback.
        touch_task = asyncio.create_task(store.touch_session(other_session.id))
        await asyncio.sleep(0.05)
        resume.set()

        with pytest.raises(RuntimeError, match="injected failure before commit"):
            await receive_task
        await touch_task
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # Reopen fresh read-only connection: 0 orphan versions/plans/receipts, other write persisted.
    with closing(sqlite3.connect(app_db)) as db:
        db.row_factory = sqlite3.Row
        exchange_versions = db.execute(
            "SELECT COUNT(*) AS n FROM versions WHERE trigger = 'exchange_receive'"
        ).fetchone()["n"]
        total_plans = db.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"]
        receipt_count = db.execute(
            "SELECT COUNT(*) AS n FROM exchange_operation_receipts"
        ).fetchone()["n"]
        other_updated = db.execute(
            "SELECT updated_at FROM sessions WHERE id = ?", (other_session.id,)
        ).fetchone()["updated_at"]

    assert exchange_versions == 0
    assert total_plans == 1
    assert receipt_count == 0
    assert other_updated != "2025-01-01T00:00:00"


@pytest.mark.asyncio
async def test_checkpoint_recovery_on_replay_and_normal_chat_after_reply_lost(tmp_path: Path):
    """Commit-then-crash before graph sync recovers on replay AND on normal chat without loss."""
    app_db = tmp_path / "desktop_recovery.db"
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        sid, bid, v1 = await _generate_trip(client)
        scope = ExchangeReceiveScope(
            dataset_id="isolated-trip-dataset",
            authorized_devices=frozenset({"mobile-device-1"}),
            authorized_targets=frozenset({(sid, bid)}),
        )
        cmd1_bytes = json.dumps(
            _make_command(travel_id=sid, branch_id=bid, expected_revision_id=v1),
            ensure_ascii=False,
        ).encode("utf-8")

        committed_v2: list[str] = []

        def _drop_before_graph_update(version_id: str) -> None:
            committed_v2.append(version_id)
            raise ConnectionResetError("reply lost after commit before graph update")

        with pytest.raises(ConnectionResetError):
            await receive_expense_create(
                store,
                cmd1_bytes,
                scope=scope,
                graph=graph,
                on_after_commit_before_reply=_drop_before_graph_update,
            )
        v2 = committed_v2[0]
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # Part A: reopen -> verify v2 has 8635 cents while graph lags -> replay recovers graph.
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        v2_detail = (await client.get(f"/api/versions/{v2}")).json()
        assert len(v2_detail["itinerary"]["expenses"]) == 1
        assert v2_detail["itinerary"]["expenses"][0]["amount_cents"] == 8635
        assert v2_detail["version"]["checkpoint_id"] is None

        stale_state = await graph.aget_state(thread_config(bid))
        assert len(stale_state.values["itinerary"].expenses) == 0

        replay_res = await receive_expense_create(store, cmd1_bytes, scope=scope, graph=graph)
        assert replay_res["ok"] is True
        assert replay_res["replayed"] is True
        assert replay_res["receipt"]["result_revision_id"] == v2

        recovered_state = await graph.aget_state(thread_config(bid))
        assert len(recovered_state.values["itinerary"].expenses) == 1
        assert recovered_state.values["itinerary"].expenses[0].amount_cents == 8635
        assert recovered_state.values["itinerary"].expenses[0].id == "mobile-lunch-1"

        v2_after = (await client.get(f"/api/versions/{v2}")).json()
        assert v2_after["version"]["checkpoint_id"] is not None

        # Part B: commit a second expense, crash before graph update, reopen, NO replay,
        # continue with a normal chat turn and confirm the new version inherits both expenses.
        cmd2_bytes = json.dumps(
            _make_command(
                travel_id=sid,
                branch_id=bid,
                expected_revision_id=v2,
                operation_id="op-mobile-dinner-2",
                entity_id="mobile-dinner-2",
                title="虚构晚餐",
                amount_cents=4250,
            ),
            ensure_ascii=False,
        ).encode("utf-8")

        committed_v3: list[str] = []

        def _drop_v3_before_graph_update(version_id: str) -> None:
            committed_v3.append(version_id)
            raise ConnectionResetError("reply lost before graph update on v3")

        with pytest.raises(ConnectionResetError):
            await receive_expense_create(
                store,
                cmd2_bytes,
                scope=scope,
                graph=graph,
                on_after_commit_before_reply=_drop_v3_before_graph_update,
            )
        v3 = committed_v3[0]
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        stale_before_chat = await graph.aget_state(thread_config(bid))
        assert len(stale_before_chat.values["itinerary"].expenses) == 1

        # Normal chat turn (changing detail_level so a genuinely new version v4 is created)
        # without replaying op-mobile-dinner-2.
        chat_res = await client.post(
            "/api/chat",
            json={
                "session_id": sid,
                "branch_id": bid,
                "message": "帮我微调第1天节奏",
                "detail_level": "L3_detailed",
            },
        )
        assert chat_res.status_code == 200, chat_res.text
        v4 = chat_res.json()["version_id"]
        assert v4 and v4 not in (v1, v2, v3)

        v1_detail = (await client.get(f"/api/versions/{v1}")).json()
        v2_detail = (await client.get(f"/api/versions/{v2}")).json()
        v3_detail = (await client.get(f"/api/versions/{v3}")).json()
        v4_detail = (await client.get(f"/api/versions/{v4}")).json()

        assert [e["amount_cents"] for e in v1_detail["itinerary"]["expenses"]] == []
        assert [e["amount_cents"] for e in v2_detail["itinerary"]["expenses"]] == [8635]
        assert [e["amount_cents"] for e in v3_detail["itinerary"]["expenses"]] == [8635, 4250]
        assert [e["amount_cents"] for e in v4_detail["itinerary"]["expenses"]] == [8635, 4250]
        assert v3_detail["version"]["checkpoint_id"] is not None
    finally:
        await _close_isolated_app(conn, cp_cm, client)


@pytest.mark.asyncio
async def test_explicit_target_authorization_rejects_wrong_branch_old_and_new_keys(
    tmp_path: Path,
):
    """Unauthorized existing fork branch (with real head) must fail before receipt/baseline."""
    app_db = tmp_path / "desktop_scope.db"
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        sid, primary_bid, v1 = await _generate_trip(client)
        fork_res = await client.post(
            f"/api/sessions/{sid}/branches",
            json={"title": "备选分支", "from_branch_id": primary_bid, "fork_version_id": v1},
        )
        assert fork_res.status_code == 200, fork_res.text
        fork_bid = next(
            item["id"] for item in fork_res.json()["items"] if item["id"] != primary_bid
        )
        fork_versions = (await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"]
        fork_head = fork_versions[-1]["id"]

        scope = ExchangeReceiveScope(
            dataset_id="isolated-trip-dataset",
            authorized_devices=frozenset({"mobile-device-1"}),
            authorized_targets=frozenset({(sid, primary_bid)}),
        )

        # Commit one expense on the authorized primary branch first.
        ok_cmd = _make_command(travel_id=sid, branch_id=primary_bid, expected_revision_id=v1)
        ok_res = await receive_expense_create(
            store, json.dumps(ok_cmd, ensure_ascii=False).encode("utf-8"), scope=scope, graph=graph
        )
        assert ok_res["ok"] is True

        # 1. Misdirected existing fork branch with its REAL current head + OLD key:
        #    must reject as unauthorized before reading receipt (not intent_conflict/replay).
        wrong_old_key = _make_command(
            travel_id=sid,
            branch_id=fork_bid,
            expected_revision_id=fork_head,
            operation_id="op-mobile-lunch-1",
            entity_id="mobile-lunch-1",
        )
        res_old = await receive_expense_create(
            store,
            json.dumps(wrong_old_key, ensure_ascii=False).encode("utf-8"),
            scope=scope,
            graph=graph,
        )
        assert res_old == {"ok": False, "error": "unauthorized"}

        # 2. Misdirected existing fork branch with its REAL current head + NEW key:
        #    must reject as unauthorized before reading baseline.
        wrong_new_key = _make_command(
            travel_id=sid,
            branch_id=fork_bid,
            expected_revision_id=fork_head,
            operation_id="op-mobile-wrong-branch-new",
            entity_id="mobile-wrong-branch-new",
        )
        res_new = await receive_expense_create(
            store,
            json.dumps(wrong_new_key, ensure_ascii=False).encode("utf-8"),
            scope=scope,
            graph=graph,
        )
        assert res_new == {"ok": False, "error": "unauthorized"}

        # 3. Non-existent branch: also rejected as unauthorized.
        missing_branch = _make_command(
            travel_id=sid,
            branch_id="non-existent-branch",
            expected_revision_id=fork_head,
            operation_id="op-mobile-missing-branch",
            entity_id="mobile-missing-branch",
        )
        res_missing = await receive_expense_create(
            store,
            json.dumps(missing_branch, ensure_ascii=False).encode("utf-8"),
            scope=scope,
            graph=graph,
        )
        assert res_missing == {"ok": False, "error": "unauthorized"}

        # Fork branch remains completely untouched.
        fork_after = (await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"]
        assert len(fork_after) == 1
        assert fork_after[0]["id"] == fork_head
        fork_detail = (await client.get(f"/api/versions/{fork_head}")).json()
        assert fork_detail["itinerary"]["expenses"] == []
    finally:
        await _close_isolated_app(conn, cp_cm, client)


@pytest.mark.asyncio
async def test_sender_pending_store_delivery_reply_lost_retry_and_confirm_after_reopen(
    tmp_path: Path,
):
    """Persist sender intent via expense_pending, lose reply, reopen both DBs, retry & confirm."""
    app_db = tmp_path / "desktop_trip.db"
    sender_db = tmp_path / "independent_sender.db"
    init_sender_store(sender_db)

    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        sid, primary_bid, v1 = await _generate_trip(client)
        scope = ExchangeReceiveScope(
            dataset_id="isolated-trip-dataset",
            authorized_devices=frozenset({"mobile-device-1"}),
            authorized_targets=frozenset({(sid, primary_bid)}),
        )
        command = _make_command(travel_id=sid, branch_id=primary_bid, expected_revision_id=v1)
        cmd_bytes = json.dumps(command, ensure_ascii=False).encode("utf-8")

        staged = stage_pending_operation(sender_db, cmd_bytes)
        assert staged["ok"] is True
        assert staged["staged"] is True
        assert staged["record"]["delivery_state"] == "unsent"
        expected_digest = staged["record"]["intent_sha256"]

        # First delivery from persisted sender store: desktop commits atomically, then reply is
        # lost before graph checkpoint update and before sender receives the receipt.
        committed_versions: list[str] = []

        def _drop_reply(version_id: str) -> None:
            committed_versions.append(version_id)
            raise ConnectionResetError("simulated reply loss after commit")

        first_attempt = await _deliver_pending_to_desktop(
            sender_db,
            store,
            graph,
            scope=scope,
            source_device_id="mobile-device-1",
            operation_id="op-mobile-lunch-1",
            on_after_commit_before_reply=_drop_reply,
        )
        assert first_attempt["ok"] is False
        assert first_attempt["error"] == "reply_lost"
        assert first_attempt["record"]["delivery_state"] == "verification_pending"
        assert first_attempt["record"]["confirmed_revision_id"] is None
        v2 = committed_versions[0]
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # Reopen both databases after reply loss: sender keeps full original intent unconfirmed.
    reopened_sender = read_pending_operation(
        sender_db,
        dataset_id="isolated-trip-dataset",
        source_device_id="mobile-device-1",
        operation_id="op-mobile-lunch-1",
    )
    assert reopened_sender["ok"] is True
    assert reopened_sender["record"]["delivery_state"] == "verification_pending"
    assert reopened_sender["record"]["confirmed_revision_id"] is None
    assert reopened_sender["record"]["intent_sha256"] == expected_digest

    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        # Retry delivery directly from persisted sender store; desktop replays durable receipt
        # and recovers checkpoint; sender persists confirmation.
        retry_attempt = await _deliver_pending_to_desktop(
            sender_db,
            store,
            graph,
            scope=scope,
            source_device_id="mobile-device-1",
            operation_id="op-mobile-lunch-1",
        )
        assert retry_attempt["ok"] is True
        assert retry_attempt["record"]["delivery_state"] == "confirmed"
        assert retry_attempt["record"]["confirmed_revision_id"] == v2
        assert retry_attempt["record"]["confirmed_effect"] == "changed"

        # Reconcile lookup on confirmed record keeps confirmed state intact.
        reconciled = await _reconcile_pending_with_desktop(
            sender_db,
            store,
            scope=scope,
            source_device_id="mobile-device-1",
            operation_id="op-mobile-lunch-1",
        )
        assert reconciled["ok"] is True
        assert reconciled["record"]["delivery_state"] == "confirmed"
        assert reconciled["record"]["confirmed_revision_id"] == v2

        # Same key with different amount (86.36 CNY) must be rejected as intent_conflict.
        diff_amount = _make_command(
            travel_id=sid, branch_id=primary_bid, expected_revision_id=v1, amount_cents=8636
        )
        conflict_res = await receive_expense_create(
            store,
            json.dumps(diff_amount, ensure_ascii=False).encode("utf-8"),
            scope=scope,
            graph=graph,
        )
        assert conflict_res == {"ok": False, "error": "intent_conflict"}

        # Stale baseline (new key referencing v1 after v2 committed) -> baseline_conflict.
        stale_cmd = _make_command(
            travel_id=sid,
            branch_id=primary_bid,
            expected_revision_id=v1,
            operation_id="op-mobile-stale-2",
            entity_id="mobile-stale-2",
        )
        stale_res = await receive_expense_create(
            store,
            json.dumps(stale_cmd, ensure_ascii=False).encode("utf-8"),
            scope=scope,
            graph=graph,
        )
        assert stale_res == {
            "ok": False,
            "error": "baseline_conflict",
            "current_revision_id": v2,
        }
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # Final reopen of BOTH databases: sender confirmed_revision_id matches desktop's unique v2,
    # and all existing trip detail / version list / export endpoints read back 86.35 CNY cleanly.
    final_sender = read_pending_operation(
        sender_db,
        dataset_id="isolated-trip-dataset",
        source_device_id="mobile-device-1",
        operation_id="op-mobile-lunch-1",
    )
    assert final_sender["ok"] is True
    assert final_sender["record"]["delivery_state"] == "confirmed"
    assert final_sender["record"]["confirmed_revision_id"] == v2

    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        versions = (await client.get(f"/api/branches/{primary_bid}/versions")).json()["items"]
        assert [item["id"] for item in versions] == [v1, v2]

        v1_detail = (await client.get(f"/api/versions/{v1}")).json()
        v2_detail = (await client.get(f"/api/versions/{v2}")).json()
        assert v1_detail["itinerary"]["expenses"] == []
        assert len(v2_detail["itinerary"]["expenses"]) == 1
        expense = v2_detail["itinerary"]["expenses"][0]
        assert expense["id"] == "mobile-lunch-1"
        assert expense["title"] == "虚构午餐"
        assert expense["amount_cents"] == 8635
        assert expense["category"] == "meal"

        export_res = await client.get(f"/api/versions/{v2}/export")
        assert export_res.status_code == 200
        assert "虚构午餐" in export_res.text
        assert "86.35" in export_res.text

        # Graph state matches v2 after reopen too.
        state = await graph.aget_state(thread_config(primary_bid))
        assert [e.amount_cents for e in state.values["itinerary"].expenses] == [8635]
    finally:
        await _close_isolated_app(conn, cp_cm, client)
