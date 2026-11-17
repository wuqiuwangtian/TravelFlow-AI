"""Pure decoded-value patch plan; independent stdlib, no persistence or auth."""
import copy
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

MUTABLE = {"title", "amount_cents", "currency", "paid_on", "category", "day", "activity_id",
           "notes", "receipt_ids", "budget_item_id", "settles_budget"}
WHITESPACE = {0x20, 0xA0, 0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, *range(0x2000, 0x200B)}


class Invalid(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Invalid(code)


def integer(value, low, high):
    if type(value) is int:
        return low <= value <= high
    return type(value) is float and math.isfinite(value) and value.is_integer() and low <= value <= high


def text(value, limit, title=False):
    require(type(value) is str, "text")
    require(len(value) <= limit and (not title or bool(value)), "text_length")
    points = [ord(character) for character in value]
    require(not any(0xD800 <= point <= 0xDFFF for point in points), "unicode")
    require(all(point != 0xFEFF and not 0x7F <= point <= 0x9F
                and (point >= 0x20 or (not title and point in (9, 10, 13))) for point in points), "text_control")
    if title:
        require(any(point not in WHITESPACE for point in points), "title_blank")


def payment_date(value):
    if value is None:
        return True
    if type(value) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def validate_patch(base, patch):
    require(type(patch) is dict, "patch_shape")
    require(patch.keys() <= MUTABLE | {"allow_shared_receipts"}, "unknown_field")
    for key, value in patch.items():
        if key in ("title", "notes"):
            text(value, 100 if key == "title" else 1000, key == "title")
        elif key == "amount_cents":
            require(integer(value, 1, 9999999999), "amount")
        elif key == "currency":
            require(value == "CNY" or (value is None and base.get("currency") is None), "currency")
        elif key == "paid_on":
            require(payment_date(value), "date")
        elif key == "category":
            require(value in ("transport", "lodging", "meal", "ticket", "shopping", "other"), "category")
        elif key == "day":
            require(value is None or integer(value, 1, 60), "day")
        elif key in ("activity_id", "budget_item_id"):
            require(value is None or (type(value) is str and 0 < len(value) <= (100 if key == "activity_id" else 150)),
                    "activity" if key == "activity_id" else "budget")
        elif key in ("settles_budget", "allow_shared_receipts"):
            require(type(value) is bool, "boolean")
        elif key == "receipt_ids":
            require(type(value) is list and len(value) <= 6 and all(type(item) is str and re.fullmatch(r"[a-f0-9]{32}", item) for item in value)
                    and len(set(value)) == len(value), "receipts")
            require("allow_shared_receipts" in patch, "receipt_control")


def evaluate(entry):
    base, patch, context = (entry["input"][key] for key in ("base", "patch", "context"))
    try:
        validate_patch(base, patch)
        candidate = copy.deepcopy(base)
        candidate.update({key: copy.deepcopy(value) for key, value in patch.items() if key in MUTABLE})
        require(integer(candidate["amount_cents"], 1, 9999999999), "amount")
        if "amount_cents" in patch or "currency" in patch:
            require(candidate.get("currency") in (None, "CNY"), "currency")
            require(base.get("currency") is None or base["currency"] == candidate.get("currency"), "currency_conversion")
        require(candidate["day"] is None or candidate["day"] in context["days"], "day_link")
        require(candidate["activity_id"] is None or candidate["activity_id"] in context["actual_activity_ids"], "activity_link")
        require(not candidate["settles_budget"] or candidate["budget_item_id"] is not None, "budget_link")
        if candidate["budget_item_id"] is not None:
            budget = next((row for row in context["budget_rows"] if row["id"] == candidate["budget_item_id"]), None)
            require(budget is not None and not budget["id"].startswith("expense-") and budget["category"] == candidate["category"], "budget_link")
        for kind in ("refund", "reimbursement"):
            claimed = sum(item["amount_cents"] for item in context["adjustments"] if item["expense_id"] == base["id"] and item["kind"] == kind)
            require(claimed <= candidate["amount_cents"], "recovery_limit")
        if "receipt_ids" in patch:
            for receipt_id in candidate["receipt_ids"]:
                if receipt_id in base["receipt_ids"]:
                    continue
                require(receipt_id in context["available_receipt_ids"], "receipt_unavailable")
                require(receipt_id not in context["other_receipt_ids"] or patch["allow_shared_receipts"], "receipt_shared")
        changed = any((key in base) != (key in candidate) or base.get(key) != candidate.get(key) for key in MUTABLE)
        return {"accepted": True, "value": {"candidate": candidate,
                "controls": {"allow_shared_receipts": patch["allow_shared_receipts"]} if "allow_shared_receipts" in patch else {},
                "planned_business_revision": "new" if changed else "unchanged",
                "planned_operation_receipt": "required-not-persisted", "cny_totals_eligible": candidate.get("currency") == "CNY"}}
    except Invalid as error:
        return {"accepted": False, "error": str(error)}


if __name__ == "__main__":
    corpus = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    results = {}
    for entry in corpus["cases"]:
        base = copy.deepcopy(corpus["baseline"]["base"])
        base.update(entry.get("base_overrides", {}))
        for key in entry.get("base_omit", []):
            base.pop(key, None)
        context = copy.deepcopy(corpus["baseline"]["context"])
        context.update(entry.get("context_overrides", {}))
        expanded = {"input": {"base": base, "patch": entry["patch"], "context": context}}
        before = copy.deepcopy(expanded)
        results[entry["id"]] = evaluate(expanded)
        if expanded != before:
            raise RuntimeError("Patch candidate mutated input")
    print(json.dumps({"results": results,
                      "python": sys.version.split()[0], "stdlib_only": True}, ensure_ascii=True))
