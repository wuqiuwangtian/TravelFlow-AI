"""Explicit desktop cross-check of dcba911's frozen corpus. No application entry point.

Inputs are small, fixed review vectors. This adapter checks envelope, key and
integer fields; the complete mobile field/resource policy is tested by its own runner.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest
from app.domain.itinerary import Itinerary
from p1_json_review import Rejected, TokenReader, check_tree

PROFILE = "p1-host-intent-c14n-1"
SAFE = 2**53 - 1
OPERATION = {"dataset_id", "source_device_id", "operation_id"}
TARGET = {"travel_id", "branch_id", "entity_id"}
EXPENSE = set(
    (
        "title",
        "notes",
        "currency",
        "amount_cents",
        "paid_on",
        "category",
        "day",
        "activity_id",
        "receipt_ids",
        "allow_shared_receipts",
        "budget_item_id",
        "settles_budget",
    )
)
ADJUSTMENT = {"expense_id", "amount_cents", "kind", "currency", "status", "notes"}
CLOCK = {"start_time", "start_day_offset", "duration_minutes", "notes"}
# Required and optional fields are disjoint.
FIELDS = {
    "expense.create": (EXPENSE, set()),
    "expense.patch": (set(), EXPENSE),
    "expense.delete": (set(), set()),
    "expense.restore": ({"source", "allow_shared_receipts"}, {"link_overrides"}),
    "adjustment.create": (ADJUSTMENT, set()),
    "adjustment.patch": (set(), ADJUSTMENT),
    "adjustment.delete": (set(), set()),
    "adjustment.restore": ({"source"}, {"link_overrides"}),
    "booking.set": (
        CLOCK
        | {
            "local_start",
            "time_zone",
            "utc_offset",
            "start_instant",
            "duration_semantics",
            "resolution_basis",
        },
        set(),
    ),
    "booking.clear": (set(), set()),
    "booking.legacy.patch": (set(), CLOCK),
    "booking.notes.patch": ({"notes"}, set()),
    "progress.set": ({"progress"}, set()),
}


def require(ok, error):
    if not ok:
        raise Rejected(error)


def integer(token):
    require(len(token) <= 64, "resource_number")
    mantissa, _, exponent = token.lower().partition("e")
    whole, _, fraction = mantissa.lstrip("-").partition(".")
    coefficient = int(whole + fraction)
    if coefficient == 0:
        return 0
    power = int(exponent or "0") - len(fraction)
    # Bound the resulting width before computing any power of ten.
    require(1 <= len(str(coefficient)) + power <= 16, "integer_range")
    if power < 0:
        coefficient, remainder = divmod(coefficient, 10**-power)
        require(remainder == 0, "integer_range")
    else:
        coefficient *= 10**power
    require(coefficient <= SAFE, "integer_range")
    return -coefficient if mantissa.startswith("-") else coefficient


def read_value(node):
    tag = node[0]
    if tag == "object":
        # UTF-16BE byte order compares unsigned 16-bit code units.
        pairs = sorted(node[1], key=lambda pair: pair[0].encode("utf-16-be"))
        return {key: read_value(value) for key, value in pairs}
    if tag == "array":
        return [read_value(value) for value in node[1]]
    if tag == "number":
        return integer(node[1])
    return None if tag == "null" else node[1]


def shape(value, required, optional=()):
    require(
        isinstance(value, dict) and required <= value.keys() <= required.union(optional), "shape"
    )


def identifiers(value, keys):
    shape(value, keys)
    for item in value.values():
        require(
            isinstance(item, str) and 1 <= len(item) <= 256 and len(item.encode()) <= 1024,
            "identity",
        )


def check_envelope(value, channel):
    require(channel in ("control", "receipt-query"), "canonical_channel")
    query = channel == "receipt-query"
    keys = {"profile", "kind", "operation"}
    shape(value, keys if query else keys | {"target", "expected_revision_id", "payload"})
    identifiers(value["operation"], OPERATION)
    require(
        value["profile"] == ("p1-host-query-1" if query else "p1-host-control-1"),
        "unsupported_profile",
    )
    if query:
        require(value["kind"] == "receipt.lookup", "unsupported_command")
        return
    require(value["kind"] in FIELDS, "unsupported_command")
    identifiers(value["target"], TARGET)
    identifiers({"revision": value["expected_revision_id"]}, {"revision"})
    payload = value["payload"]
    shape(payload, *FIELDS[value["kind"]])
    for key, low, high in (
        ("amount_cents", 1, 9999999999),
        ("day", 1, 60),
        ("start_day_offset", 0, 60),
        ("duration_minutes", 1, 1440),
    ):
        if key not in payload or (key == "day" and payload[key] is None):
            continue
        require(type(payload[key]) is int, "type")
        require(low <= payload[key] <= high, "integer_range")
    if "source" in payload:
        identifiers(payload["source"], {"dataset_id", "travel_id", "branch_id", "revision_id"})
    if "resolution_basis" in payload:
        identifiers(
            payload["resolution_basis"],
            {"calendar_revision_id", "zone_catalogue_id", "tzdata_version"},
        )


def artifact(raw, channel):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Rejected("encoding") from None
    tree = TokenReader(text).document()
    check_tree(tree)
    value = read_value(tree)
    primitive = channel == "integer-json-fixture"
    if not primitive:
        check_envelope(value, channel)
    document = {
        "canonical_profile": PROFILE,
        "channel": channel,
        "value" if primitive else "intent": value,
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    require(len(encoded) <= 65536, "canonical_bytes")
    return value, encoded, hashlib.sha256(encoded).hexdigest()


@pytest.fixture(scope="module")
def corpus():
    data = Path(os.environ["P1_CANONICAL_CASES"]).read_bytes()
    assert (
        hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        == "28d46d749cbf9d944c7cda35180d5f1df5bb5cd7"
    )
    return json.loads(data)


def test_frozen_inputs_and_relations(corpus):
    outputs = {}
    for entry in corpus["cases"]:
        case = entry["id"]
        raw = (
            entry["raw_json"].encode()
            if "raw_json" in entry
            else bytes.fromhex(entry["raw_utf8_hex"])
        )
        expected = entry["expected"]
        channel = entry.get("channel", "integer-json-fixture")
        if not expected["accepted"]:
            with pytest.raises(Rejected) as error:
                artifact(raw, channel)
            assert str(error.value) == expected["error"], case
            continue
        value, encoded, digest = artifact(raw, channel)
        if "validated_intent" in entry:
            assert value == entry["validated_intent"], case
        assert (
            encoded
            == expected["canonical_json"].encode()
            == bytes.fromhex(expected["canonical_utf8_hex"])
        ), case
        assert digest == expected["sha256"], case
        outputs[case] = (encoded, digest)
    assert len(corpus["cases"]) == 103 and len(outputs) == 85
    assert len(corpus["relations"]) == 18
    for relation in corpus["relations"]:
        values = [outputs[case] for case in relation["ids"]]
        count = 1 if relation["relation"] == "same" else len(values)
        assert all(len(set(column)) == count for column in zip(*values, strict=True)), relation


def test_command_format_rejections(corpus):
    raw = next(e["raw_json"] for e in corpus["cases"] if e["id"] == "CN041")
    marker = '"amount_cents":100'
    assert marker in raw
    for replacement, expected in (
        ('"amount_cents":100.00000000000000001', "integer_range"),
        ('"amount_cents":true', "type"),
        ('"amount_cents":100,"observed_head":"r3"', "shape"),
        ('"amount_cents":100,"amount_cents":100', "duplicate_key"),
        ('"amount_cents":' + "1" * 65, "resource_number"),
    ):
        with pytest.raises(Rejected) as error:
            artifact(raw.replace(marker, replacement).encode(), "control")
        assert str(error.value) == expected
    with pytest.raises(Rejected, match="canonical_channel"):
        artifact(raw.encode(), "history")


def test_legacy_typed_roundtrip():
    row = {
        "id": "fixture",
        "title": "test",
        "amount_cents": 1,
        "currency": None,
        "paid_on": None,
        "fixture_extra": "keep",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    doc = Itinerary.model_validate_json(json.dumps({"expenses": [row]}))
    emitted = json.loads(doc.model_dump_json())["expenses"][0]
    assert emitted["id"] == row["id"]
    assert {"currency", "paid_on", "fixture_extra"}.isdisjoint(emitted)
