"""Independent stdlib raw-byte candidate; no desktop imports or storage."""
import json
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


class CandidateError(Exception):
    pass


@dataclass
class RawNumber:
    lexeme: str


@dataclass
class ObjectPairs:
    entries: list


def reject_constant(_):
    raise CandidateError("json")


def scalar_string(value):
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise CandidateError("unicode")
    return value


def diagnostic(value):
    if isinstance(value, ObjectPairs):
        seen = set()
        entries = []
        for key, child in value.entries:
            scalar_string(key)
            if key in seen:
                raise CandidateError("duplicate_key")
            seen.add(key)
            entries.append([key, diagnostic(child)])
        return ["object", entries]
    if isinstance(value, RawNumber):
        return ["number", value.lexeme]
    if isinstance(value, list):
        return ["array", [diagnostic(child) for child in value]]
    if isinstance(value, str):
        return ["string", scalar_string(value)]
    if isinstance(value, bool):
        return ["boolean", value]
    if value is None:
        return ["null"]
    raise TypeError("Unexpected parser value")


def decode(raw):
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CandidateError("encoding") from error
    try:
        # Retain all tokens and property occurrences; validate after syntax has
        # succeeded, matching Node's refusal to consume a recovered syntax tree.
        value = json.loads(text, parse_int=RawNumber, parse_float=RawNumber,
                           parse_constant=reject_constant, object_pairs_hook=ObjectPairs)
    except json.JSONDecodeError as error:
        raise CandidateError("json") from error
    return diagnostic(value)


def money(tree):
    if tree[0] != "object":
        raise CandidateError("shape")
    fields = dict(tree[1])
    if set(fields) != {"currency", "amount_cents"}:
        raise CandidateError("shape")
    if fields["currency"] != ["string", "CNY"]:
        raise CandidateError("currency")
    amount = fields["amount_cents"]
    if amount[0] != "number":
        raise CandidateError("amount")
    try:
        # Decimal construction is exact and independent of the active precision.
        exact = Decimal(amount[1])
        if not 1 <= exact <= 9999999999 or exact != exact.to_integral_value():
            raise CandidateError("amount")
        return {"currency": "CNY", "amount_cents": int(exact)}
    except InvalidOperation as error:
        raise CandidateError("amount") from error


def evaluate(entry):
    raw = (entry["raw_json"].encode("utf-8") if "raw_json" in entry
           else bytes.fromhex(entry["raw_utf8_hex"]))
    try:
        tree = decode(raw)
        if entry["profile"] == "json":
            return {"accepted": True, "tree": tree}
        if entry["profile"] == "cny-amount":
            return {"accepted": True, "value": money(tree)}
        raise ValueError("Unknown candidate profile")
    except CandidateError as error:
        return {"accepted": False, "error": str(error)}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/check-p1-json.py candidate.json")
    corpus = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps({"results": {entry["id"]: evaluate(entry) for entry in corpus["cases"]},
                      "python": sys.version.split()[0], "stdlib_only": True}, ensure_ascii=True))
