"""Explicit 0ff3913 review; preserves 3100d32 evidence and uses no business storage."""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from p1_values_review import CORPUS, desktop_reference, run_node

ROOT = Path(__file__).with_name("fixtures") / "p1_values_0ff3913"
BOUNDARIES = json.loads((ROOT / "boundaries.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "time-zone-ids.json").read_text(encoding="utf-8"))
ZONES = frozenset(REGISTRY["time_zone_ids"])
ENTRIES = CORPUS["cases"] + BOUNDARIES["cases"]


@pytest.fixture(scope="module")
def mobile():
    spec = importlib.util.spec_from_file_location(
        "pinned_values_0ff3913", ROOT / "mobile_values.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.load_zone_ids(ROOT / "time-zone-ids.json") == ZONES
    return module


@pytest.fixture(scope="module")
def node_results():
    return run_node(ENTRIES, ROOT / "mobile_values.mjs")["results"]


@pytest.mark.parametrize(
    "name,blob",
    [
        ("boundaries.json", "d56491142e61b93c5d86aa78934542c133d22c91"),
        ("mobile_values.mjs", "198d960d4b2fd43005b944a171d0e4d6d056f18b"),
        ("mobile_values.py", "21426a1ad83239cb4ca3e5e1b47ee70851b2ec12"),
        ("time-zone-ids.json", "ef05011f7c4687617a7db6215d32215c2965b2e6"),
    ],
)
def test_exact_followup_source(name, blob):
    raw = (ROOT / name).read_bytes()
    assert hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() == blob


def test_followup_manifest_and_isolated_registry_scope():
    assert len({entry["id"] for entry in ENTRIES}) == len(ENTRIES) == 88
    assert [entry["id"] for entry in BOUNDARIES["cases"]] == [f"E{i:02}" for i in range(1, 15)]
    assert sum(entry["expected"]["accepted"] for entry in ENTRIES) == 29
    assert BOUNDARIES["shared_contract_version"] is None
    assert BOUNDARIES["acceptance"]["storage_execution"] == "not-implemented"
    assert REGISTRY["registry_format_version"] == 1
    assert REGISTRY["status"] == "candidate-name-registry"
    assert {"America/New_York", "Australia/Lord_Howe", "Etc/GMT+12", "Etc/GMT-14", "UTC"} == ZONES


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_followup_88_three_implementations(entry, mobile, node_results):
    before = copy.deepcopy(entry)
    assert desktop_reference(entry, zone_ids=ZONES) == entry["expected"]
    assert mobile.evaluate(entry, ZONES) == entry["expected"]
    assert node_results[entry["id"]] == entry["expected"]
    assert entry == before


@pytest.mark.parametrize("corpus", ["../p1_values_3100d32/cases.json", "boundaries.json"])
def test_python_cli_uses_the_explicit_shared_registry(corpus):
    path = ROOT / corpus
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "mobile_values.py"),
            str(path),
            str(ROOT / "time-zone-ids.json"),
        ],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["booking_zone_ids"] == sorted(ZONES)
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    assert result["results"] == {entry["id"]: entry["expected"] for entry in cases}
    assert result["stdlib_only"]


def test_new_registry_rejects_unregistered_parseable_names_without_changing_legacy_scope(mobile):
    base = next(entry for entry in ENTRIES if entry["id"] == "BK04")
    cases = [
        {**copy.deepcopy(base), "id": f"ZONE-{i}", "input": {**base["input"], "time_zone": zone}}
        for i, zone in enumerate(
            ("Etc/UTC", "GMT", "Asia/Shanghai", "america/new_york", "US/Eastern")
        )
    ]
    node = run_node(cases, ROOT / "mobile_values.mjs")["results"]
    for entry in cases:
        expected = {"accepted": False, "error": "zone"}
        assert (
            mobile.evaluate(entry, ZONES)
            == desktop_reference(entry, zone_ids=ZONES)
            == node[entry["id"]]
            == expected
        )
    # The explicit test registry is not a declaration of product/global IANA support.
