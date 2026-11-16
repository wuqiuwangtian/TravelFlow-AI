"""Explicit 6ab3a38 raw-byte review; no production decoder or storage.

Requires an explicitly supplied, integrity-pinned jsonc-parser npm archive.
The reference uses a small recursive-descent token reader, not json.loads hooks.
"""

import base64
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

ROOT = Path(__file__).with_name("fixtures") / "p1_json_6ab3a38"
CORPUS = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
PARSER_SRI = (
    "HUgH65KyejrUFPvHFPbqOY0rsFip3Bo5wb4ngvdi1EpCYWUQDC5V+Y7mZws+DLkr4M//zQJoanu1SP+87Dv1oQ=="
)
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


class Rejected(Exception):
    pass


class TokenReader:
    def __init__(self, text):
        self.text, self.pos = text, 0

    def whitespace(self):
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def take(self, char):
        self.whitespace()
        if not self.text.startswith(char, self.pos):
            raise Rejected("json")
        self.pos += len(char)

    def string(self):
        self.whitespace()
        if not self.text.startswith('"', self.pos):
            raise Rejected("json")
        try:
            value, self.pos = json.decoder.scanstring(self.text, self.pos + 1, True)
            return value
        except (ValueError, json.JSONDecodeError):
            raise Rejected("json") from None

    def value(self):
        self.whitespace()
        if self.pos == len(self.text):
            raise Rejected("json")
        char = self.text[self.pos]
        if char == '"':
            return ["string", self.string()]
        if char == "{":
            self.pos += 1
            pairs = []
            self.whitespace()
            if not self.text.startswith("}", self.pos):
                while True:
                    key = self.string()
                    self.take(":")
                    pairs.append([key, self.value()])
                    self.whitespace()
                    if self.text.startswith("}", self.pos):
                        break
                    self.take(",")
            self.take("}")
            return ["object", pairs]
        if char == "[":
            self.pos += 1
            values = []
            self.whitespace()
            if not self.text.startswith("]", self.pos):
                while True:
                    values.append(self.value())
                    self.whitespace()
                    if self.text.startswith("]", self.pos):
                        break
                    self.take(",")
            self.take("]")
            return ["array", values]
        for spelling, result in (
            ("true", ["boolean", True]),
            ("false", ["boolean", False]),
            ("null", ["null"]),
        ):
            if self.text.startswith(spelling, self.pos):
                self.pos += len(spelling)
                return result
        match = NUMBER.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            return ["number", match[0]]
        raise Rejected("json")

    def document(self):
        tree = self.value()
        self.whitespace()
        if self.pos != len(self.text):
            raise Rejected("json")
        return tree


def scalar(value):
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise Rejected("unicode")


def check_tree(tree):
    if tree[0] == "object":
        seen = set()
        for key, child in tree[1]:
            scalar(key)
            if key in seen:
                raise Rejected("duplicate_key")
            seen.add(key)
            check_tree(child)
    elif tree[0] == "array":
        for child in tree[1]:
            check_tree(child)
    elif tree[0] == "string":
        scalar(tree[1])


def desktop_reference(entry):
    raw = (
        entry["raw_json"].encode("utf-8")
        if "raw_json" in entry
        else bytes.fromhex(entry["raw_utf8_hex"])
    )
    try:
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise Rejected("encoding") from None
        tree = TokenReader(text).document()
        check_tree(tree)
        if entry["profile"] == "json":
            return {"accepted": True, "tree": tree}
        assert entry["profile"] == "cny-amount"
        if tree[0] != "object" or {key for key, _ in tree[1]} != {"currency", "amount_cents"}:
            raise Rejected("shape")
        fields = dict(tree[1])
        if fields["currency"] != ["string", "CNY"]:
            raise Rejected("currency")
        if fields["amount_cents"][0] != "number":
            raise Rejected("amount")
        try:
            number = Decimal(fields["amount_cents"][1])
            if not number.is_finite() or not 1 <= number <= 9999999999:
                raise Rejected("amount")
            numerator, denominator = number.as_integer_ratio()
            if denominator != 1:
                raise Rejected("amount")
        except InvalidOperation:
            raise Rejected("amount") from None
        return {"accepted": True, "value": {"currency": "CNY", "amount_cents": numerator}}
    except Rejected as error:
        return {"accepted": False, "error": str(error)}


@pytest.fixture(scope="module")
def mobile():
    spec = importlib.util.spec_from_file_location("pinned_json_6ab3a38", ROOT / "mobile_decode.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NODE = r"""
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
const {evaluate}=await import(process.argv[1]);
const entries=JSON.parse(readFileSync(0,"utf8"));
const results={};
for(const entry of entries) {
  const before=JSON.stringify(entry);Object.freeze(entry);
  results[entry.id]=evaluate(entry);
  assert.equal(JSON.stringify(entry),before);
}
console.log(JSON.stringify({results,node:process.version}));
"""


@pytest.fixture(scope="module")
def node_evaluate(tmp_path_factory):
    archive = os.environ.get("P1_JSON_PARSER_TGZ")
    if not archive:
        pytest.fail(
            "Set P1_JSON_PARSER_TGZ to the jsonc-parser@3.3.1 npm archive; no dependency skip"
        )
    raw = Path(archive).read_bytes()
    assert base64.b64encode(hashlib.sha512(raw).digest()).decode() == PARSER_SRI
    stage = tmp_path_factory.mktemp("p1-json-parser")
    with tarfile.open(archive) as package:
        package.extractall(stage, filter="data")
    metadata = json.loads((stage / "package/package.json").read_text(encoding="utf-8"))
    assert metadata["name"] == "jsonc-parser" and metadata["version"] == "3.3.1"
    assert not metadata.get("dependencies")
    modules = stage / "node_modules"
    modules.mkdir()
    shutil.move(str(stage / "package"), modules / "jsonc-parser")
    candidate = stage / "mobile_decode.mjs"
    candidate.write_bytes((ROOT / "mobile_decode.mjs").read_bytes())

    def evaluate(entries):
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", NODE, candidate.as_uri()],
            input=json.dumps(entries),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return json.loads(completed.stdout)["results"]

    return evaluate


@pytest.fixture(scope="module")
def original_node_results(node_evaluate):
    return node_evaluate(CORPUS["cases"])


@pytest.mark.parametrize(
    "name,blob",
    [
        ("cases.json", "7df637b7d7e6379c14de9330562936225cc9ad24"),
        ("mobile_decode.mjs", "beb6198bf46a3cbe02abed5bc5afa63bc3aeba32"),
        ("mobile_decode.py", "0ed221d6ccdb7d49eb66bccd1d86204a0c12b8bc"),
    ],
)
def test_pinned_raw_json_sources(name, blob):
    raw = (ROOT / name).read_bytes()
    assert hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() == blob


def test_manifest():
    assert [entry["id"] for entry in CORPUS["cases"]] == [f"RJ{i:02}" for i in range(1, 59)]
    assert sum(entry["expected"]["accepted"] for entry in CORPUS["cases"]) == 17
    assert CORPUS["shared_contract_version"] is None
    assert CORPUS["acceptance"]["canonical_bytes"] == "not-implemented"


@pytest.mark.parametrize("entry", CORPUS["cases"], ids=lambda entry: entry["id"])
def test_raw_58_three_implementations(entry, mobile, original_node_results):
    before = copy.deepcopy(entry)
    assert desktop_reference(entry) == entry["expected"]
    assert mobile.evaluate(entry) == entry["expected"]
    assert original_node_results[entry["id"]] == entry["expected"]
    assert entry == before


EXTRA = [
    ("json", '{"a":[{"a":1}],"b":{"a":2}}', True, None),
    ("json", '{"a":1,"\\u0061":1}', False, "duplicate_key"),
    ("json", '{"a":[1,{"b":1,"b":2}]}', False, "duplicate_key"),
    ("json", '"\\ud800\\udc00"', True, None),
    ("json", '"\\uffff"', True, None),
    ("json", "{}\ufeff", False, "json"),
    ("json", "\u2028[]", False, "json"),
    ("json", '{"a":"\\ud800","a":1}', False, "unicode"),
    ("json", '{"a":1,"a":"\\ud800"}', False, "duplicate_key"),
    ("json", '{"a":"\\ud800",}', False, "json"),
    ("cny-amount", '{"currency":"CNY","amount_cents":1.00000000000000001}', False, "amount"),
    ("cny-amount", '{"currency":"CNY","amount_cents":0.10000000000000000000e1}', True, None),
    ("cny-amount", '{"currency":"CNY","amount_cents":10e-1}', True, None),
    ("json", "[1E+400,-0.00,0.00000000000000001]", True, None),
    ("json", '{"é":1,"e\\u0301":2}', True, None),
    ("cny-amount", '{"amount_cents":100,"currency":"CNY"}', True, None),
    (
        "cny-amount",
        '{"amount_cents":1e99999999999999999999999999999999999999,"currency":"CNY"}',
        False,
        "amount",
    ),
]


@pytest.fixture(scope="module")
def extra_node_results(node_evaluate):
    return node_evaluate(
        [
            {"id": f"X{i:02}", "profile": profile, "raw_json": raw}
            for i, (profile, raw, _, _) in enumerate(EXTRA)
        ]
    )


@pytest.mark.parametrize("index,item", list(enumerate(EXTRA)), ids=lambda item: str(item)[:40])
def test_additional_raw_cases(index, item, mobile, extra_node_results):
    profile, raw, accepted, error = item
    entry = {"id": f"X{index:02}", "profile": profile, "raw_json": raw}
    expected = desktop_reference(entry)
    assert expected["accepted"] == accepted
    if error:
        assert expected["error"] == error
    assert mobile.evaluate(entry) == extra_node_results[entry["id"]] == expected


def test_original_python_cli(mobile):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "mobile_decode.py"), str(ROOT / "cases.json")],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["stdlib_only"]
    assert result["results"] == {entry["id"]: entry["expected"] for entry in CORPUS["cases"]}
