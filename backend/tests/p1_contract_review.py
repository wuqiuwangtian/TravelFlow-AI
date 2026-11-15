"""Explicit baseline characterization for the P1 review, not a future write-contract gate.

Run this file explicitly. All writes use the existing isolated SQLite/FakeLLM fixture.
Assertions describe the reviewed desktop baseline, including gaps P1 must change.
"""

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from app.agents.checkpoint import open_checkpointer, thread_config
from app.core.errors import InvalidRequest
from app.domain.itinerary import (
    AdjustmentCreate,
    AdjustmentInput,
    ExpenseCreate,
    ExpenseInput,
    Itinerary,
)
from app.services.adjustment_service import add_adjustment, edit_adjustment
from app.services.travel_service import add_expense, edit_expense
from app.services.travel_time import minutes
from pydantic import ValidationError
from test_itinerary_api import generate
from test_receipts import upload

FIXTURE = Path(__file__).with_name("fixtures") / "p1_mobile_review_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))
STAMP = "2026-01-01T00:00:00+00:00"


def expense(**updates):
    return {
        "title": "fictional P1 payment",
        "amount": "100.01",
        "request_id": "fictional-create-001",
        **updates,
    }


def model_ledger(data):
    return Itinerary.model_validate(
        {
            "expenses": [
                {**row, "title": "fictional", "created_at": STAMP, "updated_at": STAMP}
                for row in data["expenses"]
            ],
            "adjustments": [
                {**row, "id": f"fictional-{i}", "created_at": STAMP, "updated_at": STAMP}
                for i, row in enumerate(data["adjustments"])
            ],
        }
    )


async def create(client, vid, **updates):
    response = await client.post(f"/api/versions/{vid}/expenses", json=expense(**updates))
    assert response.status_code == 200, response.text
    return response.json()


def test_mobile_fixture_is_the_exact_reviewed_blob():
    raw = FIXTURE.read_bytes()
    assert (
        hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        == "1f09ea8e0d7859a2d3f865447da542e34a2c2238"
    )
    assert [row["id"] for row in CASES["source_scenarios"]] == [f"S{i:02}" for i in range(1, 13)]


@pytest.mark.parametrize("case", CASES["projection_cases"], ids=lambda c: c["id"])
def test_python_projection_matches_mobile_review(case):
    original = json.dumps(case["input"], sort_keys=True)
    model = model_ledger(case["input"])
    before = model.model_dump(mode="json")
    assert model.ledger_summary.model_dump() == case["expected"]
    assert model.model_dump(mode="json") == before
    assert json.dumps(case["input"], sort_keys=True) == original


@pytest.mark.parametrize(
    "amount,accepted",
    [
        ("99999999.99", True),
        ("100000000.00", False),
        ("0.001", False),
        ("NaN", False),
        ("Infinity", False),
        ("0", False),
        ("-1", False),
    ],
)
def test_s01_actual_decimal_input_boundaries(amount, accepted):
    if accepted:
        data = ExpenseCreate(**expense(amount=amount))
        plan = Itinerary()
        add_expense(plan, data)
        assert plan.expenses[0].amount_cents == 9999999999
    else:
        with pytest.raises(ValidationError):
            ExpenseCreate(**expense(amount=amount))


@pytest.mark.parametrize("amount", ["1e2", " 1.00 ", ".50", "01.00", "1.230"])
def test_s01_legacy_decimal_grammar_is_broader_than_the_proposed_new_text_grammar(amount):
    assert ExpenseInput(title="fictional", amount=amount).amount > 0


def test_s01_python_model_does_not_enforce_the_cross_language_safe_integer_limit():
    plan = model_ledger(
        {"expenses": [{"id": "fictional", "amount_cents": 2**53}], "adjustments": []}
    )
    assert plan.ledger_summary.paid_cents == 2**53
    # This is outside ledger-v1's valid exchange domain, not a new write permission.


@pytest.mark.parametrize("case", CASES["calendar_cases"], ids=lambda c: c["id"])
def test_calendar_examples_only_not_payment_date_persistence(case):
    assert (
        datetime.fromisoformat(case["instant"])
        .astimezone(ZoneInfo(case["time_zone"]))
        .date()
        .isoformat()
        == case["expected_local_date"]
    )


@pytest.mark.parametrize("case", CASES["clock_cases"], ids=lambda c: c["id"])
def test_clock_examples_only_not_dst_policy(case):
    end = minutes(case["start_time"], case["start_day_offset"]) + case["duration_minutes"]
    offset, clock = divmod(end, 1440)
    assert (f"{clock // 60:02}:{clock % 60:02}", offset) == (
        case["expected_end_time"],
        case["expected_end_day_offset"],
    )


async def test_s02_s12_old_http_silently_ignores_a_proposed_payment_date(environment):
    client, _, _, _ = environment
    _, _, before = await generate(client)
    after = await create(
        client,
        before["version"]["id"],
        day=2,
        paid_on="2026-11-14",
        contract_version="fictional-unsupported",
    )
    row = after["itinerary"]["expenses"][0]
    assert row["day"] == 2 and "paid_on" not in row and "contract_version" not in row
    edited = await client.put(
        f"/api/versions/{after['version']['id']}/expenses/{row['id']}",
        json=expense(amount="100.02"),
    )
    assert edited.status_code == 200
    assert "paid_on" not in edited.json()["itinerary"]["expenses"][0]
    # paid_on is a review spelling, not a registered field. Missing remains unrepresentable.


async def test_s03_edit_delete_have_no_independent_operation_receipt(environment):
    client, store, _, _ = environment
    _, bid, before = await generate(client)
    one = await create(client, before["version"]["id"])
    assert one["itinerary"]["expenses"][0]["id"] == expense()["request_id"]
    current = one
    for _ in range(2):
        response = await client.put(
            f"/api/versions/{current['version']['id']}/expenses/{expense()['request_id']}",
            json=expense(amount="100.02"),
        )
        assert response.status_code == 200
        updated = response.json()
        assert updated["version"]["id"] != current["version"]["id"]
        current = updated
    assert len(await store.list_versions(bid)) == 4
    deleted = await client.delete(
        f"/api/versions/{current['version']['id']}/expenses/{expense()['request_id']}"
    )
    assert deleted.status_code == 200
    again = await client.delete(
        f"/api/versions/{deleted.json()['version']['id']}/expenses/{expense()['request_id']}"
    )
    assert again.status_code == 400


async def test_s04_successful_create_retry_returns_current_v3_not_original_v2(environment):
    client, store, _, _ = environment
    _, bid, before = await generate(client)
    v1 = before["version"]["id"]
    one = await create(client, v1)
    activity = one["itinerary"]["days"][0]["activities"][0]["id"]
    progressed = await client.patch(
        f"/api/versions/{one['version']['id']}/days/1/activities/{activity}/progress",
        json={"progress": "completed"},
    )
    assert progressed.status_code == 200
    retry = await client.post(f"/api/versions/{v1}/expenses", json=expense())
    assert retry.status_code == 200
    assert (
        retry.json()["version"]["id"] == progressed.json()["version"]["id"] != one["version"]["id"]
    )
    assert len(await store.list_versions(bid)) == 3


async def test_s05_retry_after_correction_conflicts_and_does_not_reapply_old_amount(environment):
    client, store, _, _ = environment
    _, bid, before = await generate(client)
    one = await create(client, before["version"]["id"])
    edited = await client.put(
        f"/api/versions/{one['version']['id']}/expenses/{expense()['request_id']}",
        json=expense(amount="100.02"),
    )
    assert edited.status_code == 200
    retry = await client.post(f"/api/versions/{before['version']['id']}/expenses", json=expense())
    assert retry.status_code == 409
    assert len(await store.list_versions(bid)) == 3
    latest = (await client.get(f"/api/versions/{edited.json()['version']['id']}")).json()
    assert latest["itinerary"]["expenses"][0]["amount_cents"] == 10002


async def test_s06_current_head_replay_after_delete_recreates_the_expense(environment):
    client, store, _, _ = environment
    _, bid, before = await generate(client)
    one = await create(client, before["version"]["id"])
    deleted = await client.delete(
        f"/api/versions/{one['version']['id']}/expenses/{expense()['request_id']}"
    )
    assert deleted.status_code == 200 and deleted.json()["itinerary"]["expenses"] == []
    replay = await create(client, deleted.json()["version"]["id"])
    assert replay["itinerary"]["expenses"][0]["id"] == expense()["request_id"]
    assert len(await store.list_versions(bid)) == 4
    assert (await client.get(f"/api/versions/{deleted.json()['version']['id']}")).json()[
        "itinerary"
    ]["expenses"] == []


async def test_s07_stale_head_new_operation_does_not_persist_or_change_graph(environment):
    client, store, graph, _ = environment
    _, bid, before = await generate(client)
    one = await create(client, before["version"]["id"])
    activity = one["itinerary"]["days"][0]["activities"][0]["id"]
    progressed = await client.patch(
        f"/api/versions/{one['version']['id']}/days/1/activities/{activity}/progress",
        json={"progress": "completed"},
    )
    assert progressed.status_code == 200
    current = progressed.json()
    retry = await client.post(
        f"/api/versions/{before['version']['id']}/expenses",
        json=expense(request_id="fictional-new-operation"),
    )
    assert retry.status_code == 409
    assert len(await store.list_versions(bid)) == 3
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].expenses[
        0
    ].id == expense()["request_id"]
    assert (await client.get(f"/api/versions/{current['version']['id']}")).json() == current


async def test_s08_unavailable_receipt_precedes_create_replay(environment, monkeypatch):
    client, store, _, _ = environment
    _, bid, before = await generate(client)
    receipt = await upload(client, before["version"]["id"])
    one = await create(client, before["version"]["id"], receipt_ids=[receipt])
    monkeypatch.setattr(store, "get_receipt", AsyncMock(return_value=None))
    retry = await client.post(
        f"/api/versions/{before['version']['id']}/expenses", json=expense(receipt_ids=[receipt])
    )
    assert retry.status_code == 400 and len(await store.list_versions(bid)) == 2
    assert (await client.get(f"/api/versions/{one['version']['id']}")).json() == one


async def test_s09_branch_local_seq_is_not_a_global_causal_order(environment):
    _, store, _, _ = environment
    session = await store.create_session("fictional local seq")
    branches = [await store.create_branch(session.id, title=f"fictional-{i}") for i in range(2)]
    latest = []
    for i, branch in enumerate(branches):
        for _ in range(12):
            version = await store.create_version(
                branch.id, checkpoint_id=f"fictional-checkpoint-{i}"
            )
        latest.append(version)
    assert latest[0].seq == latest[1].seq == 12
    assert latest[0].id != latest[1].id and latest[0].checkpoint_id != latest[1].checkpoint_id
    assert latest[0].model_dump().keys() == latest[1].model_dump().keys()
    assert "parent_revision_id" not in latest[0].model_dump()


@pytest.mark.parametrize(
    "point,graph_changed,version_count,has_plan",
    [
        ("graph", False, 1, True),
        ("create_version", True, 1, True),
        ("save_plan", True, 2, False),
        ("touch_session", True, 2, True),
    ],
)
async def test_s10_injected_failures_expose_separate_commit_boundaries(
    environment, tmp_path, monkeypatch, point, graph_changed, version_count, has_plan
):
    client, store, graph, _ = environment
    async with open_checkpointer(tmp_path / "test.db") as saver:
        monkeypatch.setattr(graph, "checkpointer", saver)
        _, bid, before = await generate(client)
        target, name = (graph, "aupdate_state") if point == "graph" else (store, point)
        monkeypatch.setattr(
            target, name, AsyncMock(side_effect=OSError("fictional interrupted save"))
        )
        with pytest.raises(OSError, match="fictional interrupted save"):
            await client.post(f"/api/versions/{before['version']['id']}/expenses", json=expense())
        versions = await store.list_versions(bid)
        assert len(versions) == version_count
        snapshot = await graph.aget_state(thread_config(bid))
        assert bool(snapshot.values["itinerary"].expenses) == graph_changed
        current = await store.get_version_detail(versions[-1].id)
        assert (current.itinerary is not None) == has_plan
        with sqlite3.connect(f"file:{tmp_path / 'test.db'}?mode=ro", uri=True) as reader:
            assert (
                reader.execute(
                    "SELECT COUNT(*) FROM versions WHERE branch_id=?", (bid,)
                ).fetchone()[0]
                == version_count
            )
            assert reader.execute(
                "SELECT COUNT(*) FROM plans WHERE version_id=?", (versions[-1].id,)
            ).fetchone()[0] == int(has_plan)
        assert (await client.get(f"/api/versions/{before['version']['id']}")).json() == before
        async with open_checkpointer(tmp_path / "test.db") as reopened:
            durable = await reopened.aget_tuple(thread_config(bid))
            assert bool(durable.checkpoint["channel_values"]["itinerary"].expenses) == graph_changed
        # Actual SQLite checkpoint + business connections; no OS crash or disk-full.


def test_s11_pending_caps_separate_kinds_and_legacy_orphans():
    plan = Itinerary()
    add_expense(plan, ExpenseCreate(**expense(amount="100")))
    add_adjustment(
        plan,
        AdjustmentCreate(
            expense_id=expense()["request_id"],
            amount="60",
            kind="refund",
            status="pending",
            request_id="fictional-refund-001",
        ),
    )
    saved = plan.model_dump(mode="json")
    with pytest.raises(InvalidRequest):
        add_adjustment(
            plan,
            AdjustmentCreate(
                expense_id=expense()["request_id"],
                amount="50",
                kind="refund",
                status="pending",
                request_id="fictional-refund-002",
            ),
        )
    assert plan.model_dump(mode="json") == saved
    with pytest.raises(InvalidRequest):
        edit_expense(plan, expense()["request_id"], ExpenseInput(title="fictional", amount="50"))
    assert plan.model_dump(mode="json") == saved
    add_adjustment(
        plan,
        AdjustmentCreate(
            expense_id=expense()["request_id"],
            amount="100",
            kind="reimbursement",
            status="received",
            request_id="fictional-reimburse-001",
        ),
    )
    assert plan.ledger_summary.reimbursed_cents == 10000
    with pytest.raises(InvalidRequest):
        add_adjustment(
            plan,
            AdjustmentCreate(
                expense_id="fictional-missing",
                amount="5",
                kind="refund",
                status="received",
                request_id="fictional-orphan-001",
            ),
        )
    legacy = model_ledger(
        {
            "expenses": [],
            "adjustments": [
                {
                    "expense_id": "fictional-missing",
                    "amount_cents": 500,
                    "kind": "refund",
                    "status": "received",
                }
            ],
        }
    )
    assert legacy.ledger_summary.refunded_cents == 500


def test_s01_adjustment_input_has_the_same_decimal_boundary():
    arguments = {
        "expense_id": "fictional",
        "kind": "refund",
        "status": "pending",
        "request_id": "fictional-refund-001",
    }
    assert AdjustmentCreate(amount="99999999.99", **arguments).amount * 100 == 9999999999
    for value in ("100000000.00", "0.001", "NaN", "Infinity", "0", "-1"):
        with pytest.raises(ValidationError):
            AdjustmentCreate(amount=value, **arguments)


def test_s11_received_transition_does_not_duplicate_adjustment_and_m4_is_writable():
    plan = Itinerary()
    add_expense(plan, ExpenseCreate(**expense(amount="100")))
    arguments = {
        "expense_id": expense()["request_id"],
        "kind": "refund",
        "amount": "70",
        "status": "pending",
        "request_id": "fictional-refund-001",
    }
    add_adjustment(plan, AdjustmentCreate(**arguments))
    assert plan.ledger_summary.net_expense_cents == 10000

    edit_adjustment(
        plan, arguments["request_id"], AdjustmentInput(**{**arguments, "status": "received"})
    )
    assert len(plan.adjustments) == 1 and plan.ledger_summary.refunded_cents == 7000
    add_adjustment(
        plan,
        AdjustmentCreate(
            expense_id=expense()["request_id"],
            kind="reimbursement",
            amount="60",
            status="received",
            request_id="fictional-reimbursement-001",
        ),
    )
    assert plan.ledger_summary.model_dump() == CASES["projection_cases"][3]["expected"]
