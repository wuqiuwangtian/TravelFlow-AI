"""End-to-end tests for desktop expense update -> atomic version + outbound expense.patch."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest
from test_exchange_receive_trip import (
    _close_isolated_app,
    _generate_trip,
    _make_command,
    _open_isolated_app,
)

from app.agents.checkpoint import thread_config
from app.domain.itinerary import ExpenseInput
from app.services.exchange_outbox import (
    DESKTOP_SOURCE_DEVICE_ID,
    list_outbound_operations,
    save_expense_patch_with_outbox,
)
from app.services.exchange_receive import ExchangeReceiveScope, receive_expense_create

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from desktop.expense_patch_candidate import (  # noqa: E402
    PatchScope,
    apply_desktop_patch,
    canonicalize_patch_intent,
    init_mobile_replica,
)


async def _seed_trip_with_mobile_expense(client, store, graph):
    """Create a real trip + fork branch and receive one confirmed mobile CNY expense."""
    sid, primary_bid, v1 = await _generate_trip(client)
    fork_res = await client.post(
        f"/api/sessions/{sid}/branches",
        json={"title": "备选分支", "from_branch_id": primary_bid, "fork_version_id": v1},
    )
    assert fork_res.status_code == 200, fork_res.text
    fork_bid = next(item["id"] for item in fork_res.json()["items"] if item["id"] != primary_bid)
    fork_v1 = (await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"][-1]["id"]

    dataset_id = "p3-d1-handoff-dataset"
    scope = ExchangeReceiveScope(
        dataset_id=dataset_id,
        authorized_devices=frozenset({"mobile-handoff-device-1"}),
        authorized_targets=frozenset({(sid, primary_bid)}),
    )
    cmd = _make_command(
        travel_id=sid,
        branch_id=primary_bid,
        expected_revision_id=v1,
        operation_id="op-mobile-metro-1",
        entity_id="android-metro-1",
        title="机场快轨",
        amount_cents=1723,
        dataset_id=dataset_id,
        source_device_id="mobile-handoff-device-1",
    )
    cmd["payload"]["category"] = "transport"
    cmd["payload"]["notes"] = "手机离线首记"
    recv = await receive_expense_create(
        store, json.dumps(cmd, ensure_ascii=False).encode("utf-8"), scope=scope, graph=graph
    )
    assert recv["ok"] is True
    v2 = recv["receipt"]["result_revision_id"]
    return sid, primary_bid, v1, v2, fork_bid, fork_v1, dataset_id


@pytest.mark.asyncio
async def test_amount_only_edit_on_mobile_expense_persists_version_and_outbound_atomically(
    tmp_path: Path,
):
    """PUT /api/versions/{v}/expenses/{id} with amount-only change commits version + outbox."""
    app_db = tmp_path / "desktop_outbox.db"
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        sid, bid, v1, v2, fork_bid, fork_v1, dataset_id = await _seed_trip_with_mobile_expense(
            client, store, graph
        )

        # Edit ONLY amount (17.23 -> 21.50) via existing desktop PUT endpoint.
        put_res = await client.put(
            f"/api/versions/{v2}/expenses/android-metro-1",
            json={
                "title": "机场快轨",
                "amount": "21.50",
                "category": "transport",
                "day": None,
                "activity_id": None,
                "notes": "手机离线首记",
                "receipt_ids": [],
                "allow_shared_receipts": False,
                "budget_item_id": None,
                "settles_budget": False,
            },
        )
        assert put_res.status_code == 200, put_res.text
        v3_detail = put_res.json()
        v3 = v3_detail["version"]["id"]
        assert v3 != v2
        assert v3_detail["itinerary"]["expenses"][0]["amount_cents"] == 2150

        before_reopen = await list_outbound_operations(store, branch_id=bid)
        assert len(before_reopen) == 1
        raw_before_reopen = before_reopen[0]["raw_command_json"].encode("utf-8")
        digest_before_reopen = before_reopen[0]["intent_sha256"]
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # Reopen fresh DB + app connection and verify exact raw bytes, versions, fork, and graph.
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        after_reopen = await list_outbound_operations(store, branch_id=bid)
        assert len(after_reopen) == 1
        out_row = after_reopen[0]
        raw_after_reopen = out_row["raw_command_json"].encode("utf-8")
        assert raw_after_reopen == raw_before_reopen
        assert out_row["intent_sha256"] == digest_before_reopen
        assert out_row["delivery_state"] == "unsent"
        assert out_row["expected_revision_id"] == v2
        assert out_row["source_revision_id"] == v3
        assert out_row["entity_id"] == "android-metro-1"
        assert out_row["dataset_id"] == dataset_id
        assert out_row["source_device_id"] == DESKTOP_SOURCE_DEVICE_ID

        # Validate exact wire & canonical digest compatibility with expense-patch-candidate.2.
        parsed_cmd, _, recomputed_sha = canonicalize_patch_intent(raw_after_reopen, "control")
        assert recomputed_sha == digest_before_reopen
        assert parsed_cmd["kind"] == "expense.patch"
        assert parsed_cmd["expected_revision_id"] == v2
        assert parsed_cmd["source_revision_id"] == v3
        assert parsed_cmd["patch"] == {"amount_cents": 2150}
        assert parsed_cmd["target"] == {
            "travel_id": sid,
            "branch_id": bid,
            "entity_id": "android-metro-1",
        }

        # Existing trip detail, version list, export, fork branch, and LangGraph checkpoint.
        versions = (await client.get(f"/api/branches/{bid}/versions")).json()["items"]
        assert [item["id"] for item in versions] == [v1, v2, v3]
        v2_read = (await client.get(f"/api/versions/{v2}")).json()
        v3_read = (await client.get(f"/api/versions/{v3}")).json()
        assert [e["amount_cents"] for e in v2_read["itinerary"]["expenses"]] == [1723]
        assert [e["amount_cents"] for e in v3_read["itinerary"]["expenses"]] == [2150]
        export_res = await client.get(f"/api/versions/{v3}/export")
        assert export_res.status_code == 200
        assert "21.50" in export_res.text

        fork_versions = (await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"]
        assert [item["id"] for item in fork_versions] == [fork_v1]
        fork_detail = (await client.get(f"/api/versions/{fork_v1}")).json()
        assert fork_detail["itinerary"]["expenses"] == []

        state = await graph.aget_state(thread_config(bid))
        assert [e.amount_cents for e in state.values["itinerary"].expenses] == [2150]
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # Cross-check: feed the exact reopened desktop outbox bytes to the mobile reference receiver.
    mobile_db = tmp_path / "mobile_replica.sqlite"
    init_mobile_replica(
        mobile_db,
        defaults={
            "dataset_id": dataset_id,
            "travel_id": sid,
            "branch_id": bid,
            "entity_id": "android-metro-1",
            "initial_local_revision_id": "m-rev-1",
            "initial_synced_desktop_revision_id": v2,
            "initial_expense": {
                "id": "android-metro-1",
                "title": "机场快轨",
                "amount_cents": 1723,
                "currency": "CNY",
                "paid_on": None,
                "category": "transport",
                "notes": "手机离线首记",
                "updated_at": "2026-11-24T12:00:00Z",
            },
        },
    )
    mobile_res = apply_desktop_patch(
        mobile_db,
        raw_after_reopen,
        scope=PatchScope(
            dataset_id=dataset_id,
            authorized_devices=frozenset({DESKTOP_SOURCE_DEVICE_ID}),
            authorized_targets=frozenset({(sid, bid)}),
        ),
    )
    assert mobile_res["ok"] is True
    assert mobile_res["receipt"]["effect"] == "changed"
    assert mobile_res["receipt"]["synced_desktop_revision_id"] == v3


@pytest.mark.asyncio
async def test_atomic_rollback_on_failure_leaves_zero_version_and_zero_outbound(tmp_path: Path):
    """Failure before commit rolls back BOTH the new desktop version and the outbound command."""
    app_db = tmp_path / "desktop_outbox_rollback.db"
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        sid, bid, v1, v2, fork_bid, fork_v1, dataset_id = await _seed_trip_with_mobile_expense(
            client, store, graph
        )
        other_session = await store.create_session("并发隔离测试会话")
        await conn.execute(
            "UPDATE sessions SET updated_at = '2025-01-01T00:00:00' WHERE id = ?",
            (other_session.id,),
        )
        await conn.commit()

        detail_v2 = await store.get_version_detail(v2)
        assert detail_v2 is not None
        payload = ExpenseInput(
            title="机场快轨",
            amount=Decimal("21.50"),
            category="transport",
            notes="手机离线首记",
        )

        paused = asyncio.Event()
        resume = asyncio.Event()

        async def _fail_before_commit():
            paused.set()
            await resume.wait()
            raise RuntimeError("injected failure before commit")

        patch_task = asyncio.create_task(
            save_expense_patch_with_outbox(
                store,
                graph,
                detail_v2,
                "android-metro-1",
                payload,
                dataset_id=dataset_id,
                on_before_commit=_fail_before_commit,
            )
        )
        await asyncio.wait_for(paused.wait(), timeout=5.0)
        touch_task = asyncio.create_task(store.touch_session(other_session.id))
        await asyncio.sleep(0.05)
        resume.set()

        with pytest.raises(RuntimeError, match="injected failure before commit"):
            await patch_task
        await touch_task
    finally:
        await _close_isolated_app(conn, cp_cm, client)

    # Reopen and verify zero new versions, zero outbound commands, and intact v2 + fork + other.
    with closing(sqlite3.connect(app_db)) as db:
        db.row_factory = sqlite3.Row
        branch_versions = [
            r["id"]
            for r in db.execute(
                "SELECT id FROM versions WHERE branch_id = ? ORDER BY seq", (bid,)
            ).fetchall()
        ]
        outbound_count = db.execute(
            "SELECT COUNT(*) AS n FROM exchange_outbound_operations"
        ).fetchone()["n"]
        other_updated = db.execute(
            "SELECT updated_at FROM sessions WHERE id = ?", (other_session.id,)
        ).fetchone()["updated_at"]

    assert branch_versions == [v1, v2]
    assert outbound_count == 0
    assert other_updated != "2025-01-01T00:00:00"

    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        v2_read = (await client.get(f"/api/versions/{v2}")).json()
        assert [e["amount_cents"] for e in v2_read["itinerary"]["expenses"]] == [1723]
        fork_versions = (await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"]
        assert [item["id"] for item in fork_versions] == [fork_v1]
    finally:
        await _close_isolated_app(conn, cp_cm, client)


@pytest.mark.asyncio
async def test_multifield_and_local_expense_edits_keep_working_without_misemitting_patch(
    tmp_path: Path,
):
    """Multi-field edits, no-op edits, and local expense edits must NOT emit amount-only patches."""
    app_db = tmp_path / "desktop_outbox_multifield.db"
    conn, store, cp_cm, graph, client = await _open_isolated_app(app_db)
    try:
        sid, bid, v1, v2, fork_bid, fork_v1, _ = await _seed_trip_with_mobile_expense(
            client, store, graph
        )

        # Create a desktop-local expense (not received from mobile).
        add_local = await client.post(
            f"/api/versions/{v2}/expenses",
            json={
                "request_id": "desktop_local_exp_1",
                "title": "本地纪念品",
                "amount": "50.00",
                "category": "shopping",
                "notes": "",
            },
        )
        assert add_local.status_code == 200, add_local.text
        v3 = add_local.json()["version"]["id"]

        # 1. Multi-field edit on mobile expense (changes BOTH amount and notes).
        multi_res = await client.put(
            f"/api/versions/{v3}/expenses/android-metro-1",
            json={
                "title": "机场快轨",
                "amount": "25.00",
                "category": "transport",
                "notes": "含行李费",
            },
        )
        assert multi_res.status_code == 200, multi_res.text
        v4 = multi_res.json()["version"]["id"]
        e_v4 = next(
            e for e in multi_res.json()["itinerary"]["expenses"] if e["id"] == "android-metro-1"
        )
        assert e_v4["amount_cents"] == 2500
        assert e_v4["notes"] == "含行李费"

        # 2. Title-only edit on mobile expense (amount unchanged).
        title_res = await client.put(
            f"/api/versions/{v4}/expenses/android-metro-1",
            json={
                "title": "首都机场快轨",
                "amount": "25.00",
                "category": "transport",
                "notes": "含行李费",
            },
        )
        assert title_res.status_code == 200, title_res.text
        v5 = title_res.json()["version"]["id"]

        # 3. Amount-only edit on desktop-local expense (never received from mobile).
        local_res = await client.put(
            f"/api/versions/{v5}/expenses/desktop_local_exp_1",
            json={
                "title": "本地纪念品",
                "amount": "62.00",
                "category": "shopping",
                "notes": "",
            },
        )
        assert local_res.status_code == 200, local_res.text
        v6 = local_res.json()["version"]["id"]

        # 4. No-op edit on mobile expense (identical fields and amount).
        noop_res = await client.put(
            f"/api/versions/{v6}/expenses/android-metro-1",
            json={
                "title": "首都机场快轨",
                "amount": "25.00",
                "category": "transport",
                "notes": "含行李费",
            },
        )
        assert noop_res.status_code == 200, noop_res.text

        outbound = await list_outbound_operations(store, branch_id=bid)
        assert outbound == []

        fork_versions = (await client.get(f"/api/branches/{fork_bid}/versions")).json()["items"]
        assert [item["id"] for item in fork_versions] == [fork_v1]
    finally:
        await _close_isolated_app(conn, cp_cm, client)
