"""Explicit 3100d32 value review: independent reference and pinned mobile evidence.

No production imports, storage, network, Android bridge or formal contract API.
Known candidate gaps below characterize a fixed baseline, not desired behavior.
"""

import copy
import hashlib
import importlib.util
import json
import math
import re
import subprocess
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

ROOT = Path(__file__).with_name("fixtures") / "p1_values_3100d32"
CORPUS = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))


class Rejected(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def fields(value, required, optional=()):
    require(type(value) is dict, "shape")
    require(
        set(required).issubset(value) and set(value).issubset(set(required) | set(optional)),
        "shape",
    )


def whole_number(value, low, high):
    if type(value) not in (int, float, Decimal):
        return False
    if type(value) is float and not math.isfinite(value):
        return False
    number = Decimal(str(value))
    return number.is_finite() and number == number.to_integral_value() and low <= number <= high


def calendar_date(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value), "date")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise Rejected("date") from None
    return value


def nonempty(value):
    return type(value) is str and len(value) > 0


def utc_string(value):
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_string(value):
    require(value.second == 0 and value.microsecond == 0, "booking_range")
    return f"{value.year:04}-{value.month:02}-{value.day:02}T{value.hour:02}:{value.minute:02}"


def desktop_reference(entry, zone_ids=None):
    """Independent review reference, intentionally not exported as product code."""
    value, kind = entry["input"], entry["kind"]
    try:
        if kind == "money_text":
            require(
                type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", value),
                "text_syntax",
            )
            require(len(value.partition(".")[0]) <= 8, "amount_range")
            cents = Decimal(value) * 100
            require(1 <= cents <= 9999999999, "amount_range")
            result = {"amount_cents": int(cents)}
        elif kind == "money_value":
            fields(value, ("currency", "amount_cents"))
            require(value["currency"] == "CNY", "currency")
            require(whole_number(value["amount_cents"], 1, 9999999999), "amount_range")
            result = {"currency": "CNY", "amount_cents": int(value["amount_cents"])}
        elif kind == "payment_date":
            mode = entry.get("mode")
            require(mode in ("create", "edit"), "mode")
            fields(value, ("paid_on",) if mode == "create" else (), ("paid_on",))
            if "paid_on" not in value:
                result = {"effect": "unchanged"}
            elif value["paid_on"] is None:
                result = {"effect": "set_unknown"}
            else:
                result = {"effect": "set_date", "paid_on": calendar_date(value["paid_on"])}
        elif kind == "elapsed_booking":
            fields(
                value,
                (
                    "local_start",
                    "time_zone",
                    "utc_offset",
                    "start_instant",
                    "duration_minutes",
                    "duration_semantics",
                ),
            )
            require(value["duration_semantics"] == "elapsed", "duration_semantics")
            require(whole_number(value["duration_minutes"], 1, 1440), "duration")
            local, offset, zone_name = value["local_start"], value["utc_offset"], value["time_zone"]
            require(
                type(local) is str
                and re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]", local
                ),
                "date",
            )
            calendar_date(local[:10])
            require(
                type(offset) is str
                and re.fullmatch(r"[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]", offset)
                and offset != "-00:00",
                "offset",
            )
            require(nonempty(zone_name) and not zone_name.startswith(("+", "-")), "zone")
            if zone_ids is not None:
                require(zone_name in zone_ids, "zone")
            try:
                zone = ZoneInfo(zone_name)
            except (ValueError, ZoneInfoNotFoundError):
                raise Rejected("zone") from None
            try:
                start = datetime.fromisoformat(local + ":00" + offset).astimezone(UTC)
                require(utc_string(start) == value["start_instant"], "instant")
                require(local_string(start.astimezone(zone)) == local, "resolution")
                end = start + timedelta(minutes=int(value["duration_minutes"]))
                zoned_end = end.astimezone(zone)
                end_local = local_string(zoned_end)
                seconds = int(zoned_end.utcoffset().total_seconds())
                require(seconds % 60 == 0, "booking_range")
            except OverflowError:
                raise Rejected("booking_range") from None
            absolute = abs(seconds) // 60
            result = {
                "start_instant": utc_string(start),
                "end_instant": utc_string(end),
                "end_local": end_local,
                "end_utc_offset": (
                    f"{'-' if seconds < 0 else '+'}{absolute // 60:02}:{absolute % 60:02}"
                ),
                "duration_semantics": "elapsed",
            }
        elif kind == "receipt_view":
            fields(
                value,
                ("operation", "committed_revision_id", "replayed", "applied_on_this_call"),
                ("observed_head",),
            )
            fields(value["operation"], ("dataset_id", "source_device_id", "operation_id"))
            require(
                all(nonempty(item) for item in value["operation"].values())
                and nonempty(value["committed_revision_id"]),
                "identity",
            )
            require(
                type(value["replayed"]) is bool
                and type(value["applied_on_this_call"]) is bool
                and value["replayed"] != value["applied_on_this_call"],
                "receipt_semantics",
            )
            if "observed_head" in value:
                head = value["observed_head"]
                fields(head, ("revision_id", "entity_state"))
                require(
                    nonempty(head["revision_id"])
                    and head["entity_state"] in ("present", "deleted", "unknown"),
                    "head",
                )
            result = copy.deepcopy(value)
        else:
            raise AssertionError(kind)
        return {"accepted": True, "value": result}
    except Rejected as error:
        return {"accepted": False, "error": str(error)}


@pytest.fixture(scope="module")
def mobile_python():
    spec = importlib.util.spec_from_file_location(
        "pinned_mobile_values_3100d32", ROOT / "mobile_values.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NODE = r"""
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
const {evaluate}=await import(process.argv[1]);
const input=JSON.parse(readFileSync(0,"utf8"));
function freeze(x) {if(x&&typeof x==="object"){Object.values(x).forEach(freeze);Object.freeze(x);}}
const results={};
for(const original of input) {
  const entry=original.raw?{kind:original.kind,input:JSON.parse(original.raw)}:original;
  const before=JSON.stringify(entry);freeze(entry.input);
  try {results[original.id]=evaluate(entry);} catch(e) {results[original.id]={unhandled:e.name};}
  assert.equal(JSON.stringify(entry),before);
}
console.log(JSON.stringify({results,node:process.version,icu:process.versions.icu,tz:process.versions.tz}));
"""


def run_node(entries, module_path=None):
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            NODE,
            (module_path or ROOT / "mobile_values.mjs").as_uri(),
        ],
        input=json.dumps(entries),
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def node_original():
    return run_node(CORPUS["cases"])["results"]


@pytest.mark.parametrize(
    "name,blob",
    [
        ("cases.json", "7c4a57a4fad775c553e9a5318107853dd561ebe1"),
        ("mobile_values.mjs", "85bc7ca4ce6cb7ee45a47f1e22ae3d18b35ffc1f"),
        ("mobile_values.py", "18957ea1d1a34839686eae297bc3ad061a188581"),
    ],
)
def test_exact_mobile_source(name, blob):
    raw = (ROOT / name).read_bytes()
    assert hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() == blob


def test_case_manifest():
    assert len({item["id"] for item in CORPUS["cases"]}) == 74
    assert Counter(item["kind"] for item in CORPUS["cases"]) == {
        "money_text": 18,
        "money_value": 12,
        "payment_date": 16,
        "elapsed_booking": 16,
        "receipt_view": 12,
    }
    assert CORPUS["shared_contract_version"] is None
    assert CORPUS["acceptance"]["desktop_candidate_execution"] == "pending"


@pytest.mark.parametrize("entry", CORPUS["cases"], ids=lambda entry: entry["id"])
def test_original_74_three_implementations(entry, mobile_python, node_original):
    before = copy.deepcopy(entry)
    assert desktop_reference(entry) == entry["expected"]
    assert mobile_python.evaluate(entry) == entry["expected"]
    assert node_original[entry["id"]] == entry["expected"]
    assert entry == before


BASE = next(entry["input"] for entry in CORPUS["cases"] if entry["id"] == "BK01")
EXTRA = [
    {
        "id": "E01",
        "kind": "elapsed_booking",
        "input": {
            **BASE,
            "local_start": "9999-12-31T10:30",
            "start_instant": "9999-12-31T15:30:00.000Z",
            "duration_minutes": 540,
        },
    },
    {
        "id": "E02",
        "kind": "elapsed_booking",
        "input": {
            **BASE,
            "local_start": "0001-01-01T00:00",
            "time_zone": "Etc/GMT+12",
            "utc_offset": "+00:00",
            "start_instant": "0001-01-01T00:00:00.000Z",
            "duration_minutes": 1,
        },
    },
    {"id": "E03", "kind": "elapsed_booking", "input": {**BASE, "time_zone": "america/new_york"}},
    {
        "id": "E04",
        "kind": "elapsed_booking",
        "input": {
            **BASE,
            "local_start": "2026-10-04T01:45",
            "time_zone": "Australia/Lord_Howe",
            "utc_offset": "+10:30",
            "start_instant": "2026-10-03T15:15:00.000Z",
        },
    },
    {"id": "E05", "kind": "payment_date", "mode": "create", "input": {"paid_on": "0001-01-01"}},
    {"id": "E06", "kind": "payment_date", "mode": "create", "input": {"paid_on": "9999-12-31"}},
]


def test_booking_counterexamples_and_additional_boundaries(mobile_python):
    """Fixed baseline findings; acceptance of a finding is not acceptance of the bug."""
    node = run_node(EXTRA)["results"]
    assert node["E01"]["accepted"] and node["E01"]["value"]["end_instant"].startswith("+010000")
    assert (
        mobile_python.evaluate(EXTRA[0])
        == desktop_reference(EXTRA[0])
        == {"accepted": False, "error": "booking_range"}
    )
    assert node["E02"] == {"accepted": False, "error": "resolution"}
    with pytest.raises(OverflowError):
        mobile_python.evaluate(EXTRA[1])
    assert desktop_reference(EXTRA[1]) == {"accepted": False, "error": "booking_range"}
    assert node["E03"]["accepted"]
    assert (
        mobile_python.evaluate(EXTRA[2])
        == desktop_reference(EXTRA[2])
        == {"accepted": False, "error": "zone"}
    )
    for entry in EXTRA[3:]:
        assert node[entry["id"]] == mobile_python.evaluate(entry) == desktop_reference(entry)
    assert node["E04"]["value"]["end_local"] == "2026-10-04T04:15"
    assert node["E04"]["value"]["end_utc_offset"] == "+11:00"


def test_raw_json_precision_and_duplicate_keys_remain_unverified(mobile_python):
    fractional = '{"currency":"CNY","amount_cents":1.00000000000000001}'
    duplicate = '{"currency":"CNY","amount_cents":1,"amount_cents":2}'
    rows = [
        {"id": "RAW1", "kind": "money_value", "raw": fractional},
        {"id": "RAW2", "kind": "money_value", "raw": duplicate},
    ]
    node = run_node(rows)["results"]
    for row in rows:
        decoded = json.loads(row["raw"])
        result = mobile_python.evaluate({"kind": "money_value", "input": decoded})
        assert result["accepted"] and node[row["id"]] == result
    exact = json.loads(fractional, parse_float=Decimal)
    assert desktop_reference({"kind": "money_value", "input": exact}) == {
        "accepted": False,
        "error": "amount_range",
    }
    pairs = json.loads(duplicate, object_pairs_hook=list)
    assert [key for key, _ in pairs].count("amount_cents") == 2
