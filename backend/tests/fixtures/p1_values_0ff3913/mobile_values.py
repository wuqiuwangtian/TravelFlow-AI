"""标准库P1值片段候选对照；不导入电脑业务，不执行写入或恢复。"""

import copy
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError


class CandidateError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise CandidateError(code)


def shape(value, required, optional=()):
    require(type(value) is dict, "shape")
    require(set(required) <= value.keys() <= set(required) | set(optional), "shape")


def integer(value, low, high):
    if type(value) is int:
        return low <= value <= high
    return type(value) is float and math.isfinite(value) and value.is_integer() and low <= value <= high


def text(value):
    return type(value) is str and bool(value)


def money_text(value):
    require(type(value) is str, "text_syntax")
    match = re.fullmatch(r"(0|[1-9][0-9]*)(?:\.([0-9]{1,2}))?", value)
    require(match is not None, "text_syntax")
    require(len(match[1]) <= 8, "amount_range")
    cents = int(match[1]) * 100 + int((match[2] or "").ljust(2, "0"))
    require(1 <= cents <= 9999999999, "amount_range")
    return {"amount_cents": cents}


def money_value(value):
    shape(value, ("currency", "amount_cents"))
    require(value["currency"] == "CNY", "currency")
    require(integer(value["amount_cents"], 1, 9999999999), "amount_range")
    return {"currency": "CNY", "amount_cents": int(value["amount_cents"])}


def valid_date(value):
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def payment_date(value, mode):
    require(mode in ("create", "edit"), "mode")
    shape(value, ("paid_on",) if mode == "create" else (), ("paid_on",) if mode == "edit" else ())
    if "paid_on" not in value:
        return {"effect": "unchanged"}
    if value["paid_on"] is None:
        return {"effect": "set_unknown"}
    require(valid_date(value["paid_on"]), "date")
    return {"effect": "set_date", "paid_on": value["paid_on"]}


def local_text(value):
    require(value.second == 0 and value.microsecond == 0, "booking_range")
    return f"{value.year:04}-{value.month:02}-{value.day:02}T{value.hour:02}:{value.minute:02}"


def as_zone(value, zone):
    try:
        return value.astimezone(zone)
    except (OverflowError, ValueError):
        raise CandidateError("booking_range") from None


def utc_text(value):
    return as_zone(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_zone_ids(path):
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = registry["time_zone_ids"]
    if (registry["registry_format_version"] != 1 or registry["status"] != "candidate-name-registry"
            or type(ids) is not list or not ids or not all(text(item) for item in ids)
            or len(ids) != len(set(ids))):
        raise RuntimeError("Invalid candidate time-zone registry")
    return frozenset(ids)


def elapsed_booking(value, zone_ids=None):
    shape(value, ("local_start", "time_zone", "utc_offset", "start_instant", "duration_minutes", "duration_semantics"))
    require(value["duration_semantics"] == "elapsed", "duration_semantics")
    require(integer(value["duration_minutes"], 1, 1440), "duration")
    local = value["local_start"]
    require(type(local) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]", local)
            and valid_date(local[:10]), "date")
    offset = value["utc_offset"]
    require(type(offset) is str and re.fullmatch(r"[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]", offset)
            and offset != "-00:00", "offset")
    if zone_ids is None:
        zone_ids = load_zone_ids(Path(__file__).resolve().parent.parent / "prototypes/p1-values/time-zone-ids.json")
    require(text(value["time_zone"]) and value["time_zone"] in zone_ids, "zone")
    try:
        zone = ZoneInfo(value["time_zone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise CandidateError("zone") from None
    start = as_zone(datetime.fromisoformat(f"{local}:00{offset}"), timezone.utc)
    require(value["start_instant"] == utc_text(start), "instant")
    require(local_text(as_zone(start, zone)) == local, "resolution")
    try:
        end = start + timedelta(minutes=int(value["duration_minutes"]))
    except OverflowError:
        raise CandidateError("booking_range") from None
    local_end = as_zone(end, zone)
    end_local = local_text(local_end)
    seconds = int(local_end.utcoffset().total_seconds())
    require(seconds % 60 == 0, "booking_range")
    absolute = abs(seconds) // 60
    end_offset = f"{'-' if seconds < 0 else '+'}{absolute // 60:02}:{absolute % 60:02}"
    return {"start_instant": value["start_instant"], "end_instant": utc_text(end),
            "end_local": end_local, "end_utc_offset": end_offset, "duration_semantics": "elapsed"}


def receipt_view(value):
    shape(value, ("operation", "committed_revision_id", "replayed", "applied_on_this_call"), ("observed_head",))
    shape(value["operation"], ("dataset_id", "source_device_id", "operation_id"))
    require(all(text(item) for item in value["operation"].values()) and text(value["committed_revision_id"]), "identity")
    require(type(value["replayed"]) is bool and type(value["applied_on_this_call"]) is bool
            and value["replayed"] != value["applied_on_this_call"], "receipt_semantics")
    if "observed_head" in value:
        shape(value["observed_head"], ("revision_id", "entity_state"))
        head = value["observed_head"]
        require(text(head["revision_id"]) and head["entity_state"] in ("present", "deleted", "unknown"), "head")
    return copy.deepcopy(value)


def evaluate(entry, zone_ids=None):
    try:
        kind = entry["kind"]
        if kind == "payment_date":
            result = payment_date(entry["input"], entry["mode"])
        elif kind == "elapsed_booking":
            result = elapsed_booking(entry["input"], zone_ids)
        else:
            functions = {"money_text": money_text, "money_value": money_value,
                         "receipt_view": receipt_view}
            result = functions[kind](entry["input"])
        return {"accepted": True, "value": result}
    except CandidateError as error:
        return {"accepted": False, "error": str(error)}


def main():
    path = Path(sys.argv[1])
    corpus = json.loads(path.read_text(encoding="utf-8"))
    registry_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "prototypes/p1-values/time-zone-ids.json"
    zone_ids = load_zone_ids(registry_path)
    before = copy.deepcopy(corpus)
    results = {entry["id"]: evaluate(entry, zone_ids) for entry in corpus["cases"]}
    if corpus != before:
        raise RuntimeError("Candidate evaluation mutated the corpus")
    tz_version = "unreported"
    for folder in TZPATH:
        version_file = Path(folder) / "tzdata.zi"
        if version_file.is_file():
            with version_file.open(encoding="utf-8") as stream:
                first = stream.readline().strip()
            if first.startswith("# version "):
                tz_version = first.removeprefix("# version ")
            break
    print(json.dumps({"results": results, "python": sys.version.split()[0],
                      "stdlib_only": True, "system_tz_version": tz_version,
                      "booking_zone_ids": sorted(zone_ids)}))


if __name__ == "__main__":
    main()
