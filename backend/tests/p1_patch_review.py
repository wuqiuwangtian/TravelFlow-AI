"""Explicit eee6cda7 pure patch review, not a default production contract gate.

Pinned mobile sources are evidence, not application imports. The desktop reference
uses only decoded fictional data, no legacy model defaults, storage or suppliers.
No claim about raw command precision, auth, locking or durable receipts is made.
"""

import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).with_name("fixtures") / "p1_patch_eee6cda7"
CORPUS = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
BUSINESS = frozenset(
    [
        "title",
        "amount_cents",
        "currency",
        "paid_on",
        "category",
        "day",
        "activity_id",
        "notes",
        "receipt_ids",
        "budget_item_id",
        "settles_budget",
    ]
)
BLANK = {32, 160, 5760, 8232, 8233, 8239, 8287, 12288, *range(8192, 8203)}
FORBIDDEN = {*range(32), *range(127, 160), 65279}


class Rejected(Exception):
    pass


def reject_unless(condition, code):
    if not condition:
        raise Rejected(code)


def integral(value, maximum):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and value == int(value)
        and 1 <= value <= maximum
    )


def submitted_text(value, title):
    reject_unless(type(value) is str, "text")
    reject_unless((1 if title else 0) <= len(value) <= (100 if title else 1000), "text_length")
    points = {ord(char) for char in value}
    reject_unless(not points.intersection(range(0xD800, 0xE000)), "unicode")
    forbidden = FORBIDDEN if title else FORBIDDEN - {9, 10, 13}
    reject_unless(not points.intersection(forbidden), "text_control")
    if title:
        reject_unless(bool(points - BLANK), "title_blank")


def valid_date(value):
    if value is None:
        return True
    if type(value) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return False
    try:
        return date(*map(int, value.split("-"))).isoformat() == value
    except ValueError:
        return False


def desktop_reference(data):
    """Independent decoded-data calculation; unknown/corrupt baselines out of scope."""
    base, patch, links = (data[key] for key in ("base", "patch", "context"))
    try:
        reject_unless(type(patch) is dict, "patch_shape")
        reject_unless(set(patch) <= BUSINESS | {"allow_shared_receipts"}, "unknown_field")
        predicates = {
            "amount_cents": (lambda v: integral(v, 9999999999), "amount"),
            "currency": (
                lambda v: v == "CNY" or (v is None and base.get("currency") is None),
                "currency",
            ),
            "paid_on": (valid_date, "date"),
            "category": (
                lambda v: v in ("transport", "lodging", "meal", "ticket", "shopping", "other"),
                "category",
            ),
            "day": (lambda v: v is None or integral(v, 60), "day"),
            "activity_id": (
                lambda v: v is None or (type(v) is str and 1 <= len(v) <= 100),
                "activity",
            ),
            "budget_item_id": (
                lambda v: v is None or (type(v) is str and 1 <= len(v) <= 150),
                "budget",
            ),
            "settles_budget": (lambda v: type(v) is bool, "boolean"),
            "allow_shared_receipts": (lambda v: type(v) is bool, "boolean"),
        }
        for field, value in patch.items():
            if field in ("title", "notes"):
                submitted_text(value, field == "title")
            elif field == "receipt_ids":
                reject_unless(type(value) is list and len(value) <= 6, "receipts")
                reject_unless(
                    all(type(x) is str and re.fullmatch(r"[a-f0-9]{32}", x) for x in value),
                    "receipts",
                )
                reject_unless(len(set(value)) == len(value), "receipts")
                reject_unless("allow_shared_receipts" in patch, "receipt_control")
            else:
                predicate, error = predicates[field]
                reject_unless(predicate(value), error)

        result = copy.deepcopy(base)
        result.update(
            copy.deepcopy({key: value for key, value in patch.items() if key in BUSINESS})
        )
        reject_unless(integral(result["amount_cents"], 9999999999), "amount")
        if {"amount_cents", "currency"}.intersection(patch):
            reject_unless(result.get("currency") in (None, "CNY"), "currency")
            reject_unless(
                base.get("currency") is None or base["currency"] == result.get("currency"),
                "currency_conversion",
            )
        reject_unless(result["day"] is None or result["day"] in links["days"], "day_link")
        reject_unless(
            result["activity_id"] is None or result["activity_id"] in links["actual_activity_ids"],
            "activity_link",
        )
        budget_id = result["budget_item_id"]
        reject_unless(not result["settles_budget"] or budget_id is not None, "budget_link")
        if budget_id is not None:
            matching = [row for row in links["budget_rows"] if row["id"] == budget_id]
            reject_unless(
                bool(matching)
                and not budget_id.startswith("expense-")
                and matching[0]["category"] == result["category"],
                "budget_link",
            )
        claims = {kind: 0 for kind in ("refund", "reimbursement")}
        for row in links["adjustments"]:
            if row["expense_id"] == base["id"] and row["kind"] in claims:
                claims[row["kind"]] += row["amount_cents"]
        reject_unless(max(claims.values()) <= result["amount_cents"], "recovery_limit")
        if "receipt_ids" in patch:
            added = [rid for rid in result["receipt_ids"] if rid not in base["receipt_ids"]]
            for rid in added:
                reject_unless(rid in links["available_receipt_ids"], "receipt_unavailable")
                reject_unless(
                    rid not in links["other_receipt_ids"] or patch["allow_shared_receipts"],
                    "receipt_shared",
                )
        changed = any(
            (key in base) != (key in result) or base.get(key) != result.get(key) for key in BUSINESS
        )
        return {
            "accepted": True,
            "value": {
                "candidate": result,
                "controls": {"allow_shared_receipts": patch["allow_shared_receipts"]}
                if "allow_shared_receipts" in patch
                else {},
                "planned_business_revision": "new" if changed else "unchanged",
                "planned_operation_receipt": "required-not-persisted",
                "cny_totals_eligible": result.get("currency") == "CNY",
            },
        }
    except Rejected as error:
        return {"accepted": False, "error": str(error)}


def expand(entry):
    data = copy.deepcopy(CORPUS["baseline"])
    data["base"].update(copy.deepcopy(entry.get("base_overrides", {})))
    for key in entry.get("base_omit", []):
        data["base"].pop(key, None)
    data["context"].update(copy.deepcopy(entry.get("context_overrides", {})))
    data["patch"] = copy.deepcopy(entry["patch"])
    return data


def expected_result(entry, base):
    wanted = entry["expected"]
    if not wanted["accepted"]:
        return wanted
    return {
        "accepted": True,
        "value": {
            "candidate": {**copy.deepcopy(base), **copy.deepcopy(wanted["entity_changes"])},
            "controls": wanted["controls"],
            "planned_business_revision": wanted["planned_business_revision"],
            "planned_operation_receipt": "required-not-persisted",
            "cny_totals_eligible": wanted["cny_totals_eligible"],
        },
    }


def extra(case_id, patch, *, error=None, changes=None, **updates):
    expected = (
        {"accepted": False, "error": error}
        if error
        else {
            "accepted": True,
            "entity_changes": changes or {},
            "controls": {"allow_shared_receipts": patch["allow_shared_receipts"]}
            if "allow_shared_receipts" in patch
            else {},
            "planned_business_revision": "new" if changes else "unchanged",
            "cny_totals_eligible": True,
        }
    )
    return {"id": case_id, "patch": patch, "expected": expected, **updates}


EXTRAS = [
    extra(f"XT{i:02}", {"title": chr(point)}, error="title_blank")
    for i, point in enumerate([0xA0, 0x1680, 0x2000, 0x200A, 0x202F, 0x205F], 1)
] + [
    extra("XT07", {"notes": "\ufeff"}, error="text_control"),
    extra("XT08", {"notes": "\x85"}, error="text_control"),
    extra("XT09", {"notes": "\udfff"}, error="unicode"),
    extra("XT10", {"notes": "😀" * 1001}, error="text_length"),
    extra("XT11", {"notes": "X\r\n\tY"}, changes={"notes": "X\r\n\tY"}),
    extra("XT12", {"receipt_ids": ["1" * 32], "allow_shared_receipts": False}),
    extra(
        "XT13",
        {"receipt_ids": ["1" * 32], "allow_shared_receipts": True},
        error="receipt_unavailable",
        base_overrides={"receipt_ids": []},
    ),
    extra("XT14", {"receipt_ids": ["3" * 32] * 7, "allow_shared_receipts": True}, error="receipts"),
    extra("XT15", {"paid_on": "2026-11-16\n"}, error="date"),
    extra("XT16", {"settles_budget": 0}, error="boolean"),
    extra("XT17", [], error="patch_shape"),
    extra("XT18", {"amount_cents": "100"}, error="amount"),
    extra("XT19", {"title": "\x1f"}, error="text_control"),
    extra("XT20", {"title": "\x7f"}, error="text_control"),
    extra("XT21", {"title": "\x9f"}, error="text_control"),
    extra(
        "XT22",
        {"receipt_ids": [f"{i:032x}" for i in range(10, 16)], "allow_shared_receipts": False},
        changes={"receipt_ids": [f"{i:032x}" for i in range(10, 16)]},
        context_overrides={"available_receipt_ids": [f"{i:032x}" for i in range(10, 16)]},
    ),
]
ENTRIES = CORPUS["cases"] + EXTRAS


@pytest.fixture(scope="module")
def mobile_python():
    spec = importlib.util.spec_from_file_location("pinned_patch_eee6cda7", ROOT / "mobile_patch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def node_results():
    script = """
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const {evaluate}=await import(process.argv[1]);
function freeze(x) {
  if(x && typeof x==='object'){Object.values(x).forEach(freeze);Object.freeze(x);}
}
const entries=JSON.parse(readFileSync(0,'utf8'));
const results={};
for(const {id,input} of entries){
  const before=structuredClone(input);freeze(input);
  results[id]=evaluate({input});assert.deepEqual(input,before);
}
process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, (ROOT / "mobile_patch.mjs").as_uri()],
        input=json.dumps([{"id": e["id"], "input": expand(e)} for e in ENTRIES]),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "name,blob",
    [
        ("cases.json", "3cebbcd12ff415d0ab8898ee6bdc606a77357035"),
        ("mobile_patch.mjs", "38de5c5ab0bbd60e401c5d38dca87167c102eea4"),
        ("mobile_patch.py", "4c4dc7ada6642261dfab77927e55d0c1c33a57a9"),
        ("mobile_check.mjs", "7f3fd2db6fea6e2eba381308bf662efedd145a24"),
    ],
)
def test_fixed_sources(name, blob):
    raw = (ROOT / name).read_bytes()
    assert hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() == blob


def test_manifest():
    assert [e["id"] for e in CORPUS["cases"]] == [f"EP{i:02}" for i in range(1, 71)]
    assert sum(e["expected"]["accepted"] for e in CORPUS["cases"]) == 33
    assert CORPUS["shared_contract_version"] is None
    assert CORPUS["desktop_review_commit"] == "e060bab3d13d62e666624cf7500adc051df389c1"
    assert CORPUS["acceptance"] == {
        "desktop_joint_review": "pending",
        "storage_execution": "not-implemented",
        "android_native_execution": "not-implemented",
    }


def test_original_mobile_entry(tmp_path):
    layout = {
        "cases.json": "docs/internal/fixtures/p1-patch-cases.json",
        "mobile_patch.mjs": "prototypes/p1-patch/expense.mjs",
        "mobile_patch.py": "scripts/check-p1-patch.py",
        "mobile_check.mjs": "scripts/check-p1-patch.mjs",
    }
    for source, target in layout.items():
        dest = tmp_path / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / source).read_bytes())
    result = subprocess.run(
        ["node", str(tmp_path / "scripts/check-p1-patch.mjs")],
        env={**os.environ, "P1_REVIEW_PYTHON": sys.executable},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    report = json.loads(result.stdout)
    assert (report["candidate_patch_cases"], report["accepted"], report["rejected"]) == (70, 33, 37)
    assert all(
        report[field] is False
        for field in (
            "raw_command_decoder_verified",
            "resource_limits_verified",
            "canonical_bytes_verified",
            "durable_receipts_verified",
            "storage_executed",
            "android_native_executed",
        )
    )


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["id"])
def test_three_way_patch_plans(entry, mobile_python, node_results):
    data = expand(entry)
    before = copy.deepcopy(data)
    expected = expected_result(entry, data["base"])
    assert desktop_reference(data) == expected
    assert data == before
    assert mobile_python.evaluate({"input": data}) == expected
    assert data == before
    assert node_results[entry["id"]] == expected
