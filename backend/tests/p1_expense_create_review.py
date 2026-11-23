"""Explicit review of one candidate command against desktop encoding and expense rules."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from p1_canonical_review import artifact
from p1_json_review import Rejected

from app.core.errors import ConflictError
from app.domain.itinerary import ExpenseCreate, Itinerary
from app.services.travel_service import add_expense

FIXTURE = Path(__file__).with_name("fixtures") / "p1_expense_create_candidate.json"


def test_candidate_encoding_and_query_scope():
    fixture = json.loads(FIXTURE.read_bytes())
    command = fixture["command"]
    _, encoded, digest = artifact(json.dumps(command).encode(), "control")
    assert artifact(json.dumps(command, sort_keys=True).encode(), "control")[1:] == (
        encoded,
        digest,
    )
    query, _, query_digest = artifact(
        json.dumps(fixture["receipt_query"]).encode(), "receipt-query"
    )
    assert query["operation"] == command["operation"]
    assert query_digest != digest
    command["payload"]["allow_shared_receipts"] = True
    assert artifact(json.dumps(command).encode(), "control")[2] != digest


@pytest.mark.parametrize("amount", [0, True, 1.25, 10_000_000_000])
def test_candidate_rejects_invalid_cents(amount):
    command = json.loads(FIXTURE.read_bytes())["command"]
    command["payload"]["amount_cents"] = amount
    with pytest.raises(Rejected):
        artifact(json.dumps(command).encode(), "control")


def test_legacy_domain_create_preserves_entity_and_exact_amount():
    command = json.loads(FIXTURE.read_bytes())["command"]
    payload = command["payload"].copy()
    # This desktop model has no independent currency or payment-date fields.
    assert payload.pop("currency") == "CNY"
    assert payload.pop("paid_on") is None
    amount = Decimal(payload.pop("amount_cents")).scaleb(-2)
    expense = ExpenseCreate(request_id=command["target"]["entity_id"], amount=amount, **payload)
    itinerary = Itinerary()
    assert add_expense(itinerary, expense)
    assert not add_expense(itinerary, expense)
    with pytest.raises(ConflictError):
        add_expense(itinerary, expense.model_copy(update={"amount": Decimal("86.36")}))
    assert [(entry.id, entry.amount_cents) for entry in itinerary.expenses] == [
        ("fixture-lunch", 8635)
    ]
