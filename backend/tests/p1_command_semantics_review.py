"""Explicit 30077dbe review probes; no registered command schema or executor.

Q probes compare illustrative, already-shaped intents only. They do not implement
capability/auth checks, resource guards, persistence, replay or canonical bytes.
Legacy tests use fictional objects or the temporary SQLite/FakeLLM ASGI fixture.
Run explicitly; these baseline observations are not future production gates.
"""

import json
import subprocess
from datetime import datetime
from decimal import Decimal

import pytest
from app.api.routes_itinerary import BookingUpdate, ProgressUpdate
from app.core.errors import InvalidRequest
from app.domain.itinerary import (
    AdjustmentInput,
    Booking,
    DayPlan,
    ExpenseInput,
    Itinerary,
    ScheduledActivity,
)
from app.services import travel_service
from app.services.replan_service import record_booking
from app.services.travel_service import expense_values
from p1_contract_review import create, expense
from p1_json_review import TokenReader, check_tree
from pydantic import ValidationError
from test_itinerary_api import generate
from test_receipts import upload


def sample(payload, *, kind="expense.edit", **updates):
    """Review labels, deliberately unregistered; not an API payload builder."""
    return {
        "profile": "review-only",
        "dataset_id": "fictional-dataset",
        "source_device_id": "fictional-device",
        "operation_id": "fictional-operation",
        "target": {"trip_id": "trip", "branch_id": "branch", "entity_id": "entity"},
        "kind": kind,
        "expected_revision_id": "r1",
        "payload": payload,
        **updates,
    }


def comparison_probe(raw):
    """Structural comparison ONLY, on bounded samples supplied by this file.

    Preserve absent/null, array order and decoded Unicode scalar sequences.
    Normalize only the reviewed money field in expense/adjustment payloads.
    Sorted object tuples aid comparison; they are NOT canonical bytes or a hash.
    No complete type/unknown-field/size validation is implied by acceptance here.
    """
    tree = TokenReader(raw).document()
    check_tree(tree)
    fields = dict(tree[1])
    money_command = fields["kind"][1] in {
        "expense.create",
        "expense.edit",
        "adjustment.create",
        "adjustment.edit",
    }

    def visit(node, path=()):
        tag, *rest = node
        if path == ("payload", "amount_cents") and money_command:
            if tag != "number":
                raise ValueError("amount")
            number = Decimal(rest[0])
            if not 1 <= number <= 9999999999 or number != number.to_integral_value():
                raise ValueError("amount")
            return ("review-money-integer", int(number))
        if tag == "object":
            return (tag, tuple(sorted((key, visit(value, (*path, key))) for key, value in rest[0])))
        if tag == "array":
            values = tuple(visit(value, (*path, index)) for index, value in enumerate(rest[0]))
            if path == ("payload", "receipt_ids") and len(set(values)) != len(values):
                raise ValueError("duplicate_receipt")
            return (tag, values)
        return (tag, *rest)

    return visit(tree)


def semantic(payload, **updates):
    return comparison_probe(json.dumps(sample(payload, **updates), ensure_ascii=False))


@pytest.mark.parametrize("lexeme", ["100", "100.0", "1e2"])
def test_q01_exact_money_equivalence(lexeme):
    raw = json.dumps(sample({"amount_cents": 100})).replace(
        '"amount_cents": 100', f'"amount_cents": {lexeme}'
    )
    assert comparison_probe(raw) == semantic({"amount_cents": 100})


def test_q02_fraction_not_rounded():
    raw = json.dumps(sample({"amount_cents": 1})).replace(
        '"amount_cents": 1', '"amount_cents": 1.00000000000000001'
    )
    with pytest.raises(ValueError, match="amount"):
        comparison_probe(raw)


def test_q03_object_order_and_equivalent_escape():
    original = sample({"title": "虚构", "amount_cents": 100})
    reordered = dict(reversed(list(original.items())))
    reordered["payload"] = dict(reversed(list(original["payload"].items())))
    assert comparison_probe(json.dumps(original, ensure_ascii=False)) == comparison_probe(
        json.dumps(reordered)
    )


def test_q04_absent_null_date_and_empty_receipts():
    values = [{}, {"paid_on": None}, {"paid_on": "2026-11-16"}, {"receipt_ids": []}]
    assert len({semantic(value) for value in values}) == len(values)


def test_q05_control_flag_is_intent():
    assert semantic({"allow_shared_receipts": False}) != semantic({"allow_shared_receipts": True})


@pytest.mark.parametrize(
    "field,value",
    [
        ("budget_item_id", "budget"),
        ("settles_budget", True),
        ("activity_id", "activity"),
        ("day", 2),
    ],
)
def test_q06_each_link_participates(field, value):
    assert semantic({}) != semantic({field: value})


def test_q07_receipt_order_and_duplicate():
    a, b = "a" * 32, "b" * 32
    assert semantic({"receipt_ids": [a, b]}) != semantic({"receipt_ids": [b, a]})
    with pytest.raises(ValueError, match="duplicate_receipt"):
        semantic({"receipt_ids": [a, a]})


@pytest.mark.parametrize("field", ["title", "notes"])
@pytest.mark.parametrize("left,right", [("é", "e\u0301"), ("fictional", " fictional ")])
def test_q08_no_nfc_or_trim(field, left, right):
    assert semantic({field: left}) != semantic({field: right})


@pytest.mark.parametrize(
    "updates",
    [
        {"expected_revision_id": "r2"},
        {"kind": "expense.create"},
        {"target": {"entity_id": "other"}},
    ],
)
def test_q09_target_kind_and_baseline(updates):
    assert semantic({}) != semantic({}, **updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"utc_offset": "-05:00", "start_instant": "2026-11-01T06:30:00Z"},
        {"resolution_basis": "other-catalogue"},
    ],
)
def test_q11_booking_fold_and_resolution_basis(updates):
    payload = {
        "local_start": "2026-11-01T01:30",
        "time_zone": "America/New_York",
        "utc_offset": "-04:00",
        "start_instant": "2026-11-01T05:30:00Z",
        "resolution_basis": "review-catalogue",
    }
    assert semantic(payload, kind="booking.set") != semantic(
        {**payload, **updates}, kind="booking.set"
    )


def test_q12_status_differs():
    assert semantic({"status": "pending"}, kind="adjustment.edit") != semantic(
        {"status": "received"}, kind="adjustment.edit"
    )


def test_legacy_model_field_inventory_and_booking_required_null():
    assert set(ExpenseInput.model_fields) == {
        "title",
        "amount",
        "category",
        "day",
        "activity_id",
        "notes",
        "receipt_ids",
        "allow_shared_receipts",
        "budget_item_id",
        "settles_budget",
    }
    assert set(AdjustmentInput.model_fields) == {"expense_id", "kind", "amount", "status", "notes"}
    assert set(Booking.model_fields) == {
        "start_time",
        "start_day_offset",
        "duration_minutes",
        "notes",
    }
    assert set(ProgressUpdate.model_fields) == {"progress"}
    with pytest.raises(ValidationError):
        BookingUpdate.model_validate({})
    assert BookingUpdate.model_validate({"booking": None}).booking is None


def test_legacy_title_and_receipt_normalization():
    data = ExpenseInput(title=" fictional ", amount="1", receipt_ids=["b" * 32, "a" * 32, "b" * 32])
    values = expense_values(data)
    assert values["title"] == "fictional"
    assert values["receipt_ids"] == ["b" * 32, "a" * 32]


@pytest.mark.parametrize(
    "title,accepted,stored",
    [
        ("\x1c", True, ""),
        ("\x85", False, None),
        ("\ufeff", True, "\ufeff"),
        ("\u00a0", False, None),
        ("\u3000", False, None),
    ],
)
def test_legacy_nonblank_validation_is_not_python_strip(title, accepted, stored):
    if not accepted:
        with pytest.raises(ValidationError):
            ExpenseInput(title=title, amount="1")
        return
    assert expense_values(ExpenseInput(title=title, amount="1"))["title"] == stored


async def test_legacy_receipt_control_changes_acceptance_not_stored_values(environment):
    client, _, _, _ = environment
    _, _, detail = await generate(client)
    vid = detail["version"]["id"]
    rid = await upload(client, vid)
    first = await create(client, vid, request_id="fictional-first", receipt_ids=[rid])
    vid = first["version"]["id"]
    data = expense(request_id="fictional-second", receipt_ids=[rid])
    false_input = ExpenseInput.model_validate(data)
    true_input = ExpenseInput.model_validate({**data, "allow_shared_receipts": True})
    assert expense_values(false_input) == expense_values(true_input)
    rejected = await client.post(f"/api/versions/{vid}/expenses", json=data)
    assert rejected.status_code == 409
    accepted = await client.post(
        f"/api/versions/{vid}/expenses", json={**data, "allow_shared_receipts": True}
    )
    assert accepted.status_code == 200, accepted.text


async def test_legacy_put_omissions_replace_defaults(environment):
    client, _, _, _ = environment
    _, _, detail = await generate(client)
    vid = detail["version"]["id"]
    rid = await upload(client, vid)
    created = await create(
        client, vid, category="meal", day=1, notes="fictional keep", receipt_ids=[rid]
    )
    saved_vid = created["version"]["id"]
    row = created["itinerary"]["expenses"][0]
    response = await client.put(
        f"/api/versions/{saved_vid}/expenses/{row['id']}",
        json={"title": row["title"], "amount": "100.01"},
    )
    assert response.status_code == 200, response.text
    edited = response.json()["itinerary"]["expenses"][0]
    assert (edited["category"], edited["day"], edited["notes"], edited["receipt_ids"]) == (
        "other",
        None,
        "",
        [],
    )
    old = (await client.get(f"/api/versions/{saved_vid}")).json()["itinerary"]["expenses"][0]
    assert (old["category"], old["day"], old["notes"], old["receipt_ids"]) == (
        "meal",
        1,
        "fictional keep",
        [rid],
    )


def fictional_activity(**updates):
    return ScheduledActivity(id="fictional-activity", title="fictional stop", **updates)


def test_legacy_booking_clear_retains_activity_clock():
    activity = fictional_activity()
    itinerary = Itinerary(days=[DayPlan(day=1, activities=[activity])])
    record_booking(
        itinerary,
        1,
        activity.id,
        Booking(start_time="23:40", start_day_offset=1, duration_minutes=120),
    )
    record_booking(itinerary, 1, activity.id, None)
    assert activity.booking is None and activity.time_is_suggested
    assert (activity.start_time, activity.start_day_offset, activity.duration_minutes) == (
        "23:40",
        1,
        120,
    )


@pytest.mark.parametrize("updates", [{"progress": "completed"}, {"is_context_anchor": True}])
def test_legacy_booking_clear_protections(updates):
    activity = fictional_activity(**updates)
    itinerary = Itinerary(days=[DayPlan(day=1, activities=[activity])])
    with pytest.raises(InvalidRequest):
        record_booking(itinerary, 1, activity.id, None)


def test_legacy_progress_repeats_refresh_audit_time(monkeypatch):
    stamps = iter([datetime(2026, 1, 1), datetime(2026, 1, 2)])

    class Clock:
        @staticmethod
        def now():
            return next(stamps)

    monkeypatch.setattr(travel_service, "datetime", Clock)
    activity = fictional_activity()
    itinerary = Itinerary(days=[DayPlan(day=1, activities=[activity])])
    travel_service.set_progress(itinerary, 1, activity.id, "completed")
    first = activity.progress_updated_at
    travel_service.set_progress(itinerary, 1, activity.id, "completed")
    assert first == datetime(2026, 1, 1) and activity.progress_updated_at == datetime(2026, 1, 2)


def test_actual_python_strip_vs_node_trim_and_scalar_lengths():
    values = [
        " fictional ",
        "\x1cfictional\x1c",
        "\ufefffictional\ufeff",
        "\x85fictional\x85",
        "\u00a0fictional\u3000",
        "😀" * 100,
    ]
    script = """
const xs=JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(xs.map(s=>({
  trim:s.trim(),units:s.length,scalars:Array.from(s).length
}))));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(values)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    node = json.loads(result.stdout)
    assert [value.strip() == row["trim"] for value, row in zip(values, node, strict=True)] == [
        True,
        False,
        False,
        False,
        True,
        True,
    ]
    assert node[-1]["units"] == 200 and node[-1]["scalars"] == len(values[-1]) == 100
    assert ExpenseInput(title=values[-1], amount="1").title == values[-1]
    with pytest.raises(ValidationError):
        ExpenseInput(title="😀" * 101, amount="1")
