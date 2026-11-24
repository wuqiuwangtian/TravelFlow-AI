"""Desktop checker for portable Phase 2 expense.create acceptance vectors."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from desktop.expense_executor import (
    CallerContext,
    Rejected,
    canonicalize_intent,
    execute_command,
    init_isolated_store,
    lookup_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VECTORS = REPO_ROOT / "backend" / "tests" / "fixtures" / "p1_expense_create_vectors.json"
DEFAULT_CONTRACT_BUNDLE = REPO_ROOT / "contracts" / "expense-create-candidate"
DEFAULT_FORMAL_CONTRACT_BUNDLE = REPO_ROOT / "contracts" / "expense-create-v0.1.0"
DEFAULT_SOURCE_FIXTURE = (
    REPO_ROOT / "backend" / "tests" / "fixtures" / "p1_expense_create_candidate.json"
)
VECTOR_FORMAT = "travelflow-expense-create-vectors-candidate/0-draft"
CONTRACT_CANDIDATE_ID = "travelflow.expense-create-candidate"
CONTRACT_CANDIDATE_VERSION = "0.1.0-candidate.2"
CONTRACT_FORMAL_ID = "travelflow.expense-create"
CONTRACT_FORMAL_VERSION = "0.1.0"
SUPPORTED_CONTRACT_RELEASES = {
    (CONTRACT_CANDIDATE_ID, CONTRACT_CANDIDATE_VERSION): ("candidate", None),
    (CONTRACT_FORMAL_ID, CONTRACT_FORMAL_VERSION): ("frozen", CONTRACT_FORMAL_VERSION),
}
CONTRACT_CANDIDATE_FILES = (
    "SPEC.md",
    "canonical-digests.json",
    "desktop-assertions.json",
    "normative-vectors.json",
    "result-mapping.json",
)

FORBIDDEN_REJECT_KEYS = frozenset({"receipt", "result_revision_id", "effect", "replayed", "state"})
ALLOWED_UNAUTHORIZED_KEYS = frozenset({"ok", "error"})


def _deep_merge(base: dict, patch: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _snapshot_store(db_path: Path) -> dict:
    """Open a fresh SQLite connection and read full persisted business and receipt content."""
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        integrity = [tuple(row) for row in db.execute("PRAGMA integrity_check").fetchall()]
        head = db.execute("SELECT head_revision_id FROM branches LIMIT 1").fetchone()[0]
        revisions = {
            row["revision_id"]: {
                "parent_revision_id": row["parent_revision_id"],
                "seq": row["seq"],
                "source_operation_id": row["source_operation_id"],
            }
            for row in db.execute(
                "SELECT revision_id, parent_revision_id, seq, source_operation_id "
                "FROM revisions ORDER BY seq"
            ).fetchall()
        }
        expenses = {
            row["entity_id"]: {
                "revision_id": row["revision_id"],
                "title": row["title"],
                "amount_cents": row["amount_cents"],
                "currency": row["currency"],
                "paid_on": row["paid_on"],
                "category": row["category"],
                "day": row["day"],
                "activity_id": row["activity_id"],
                "notes": row["notes"],
                "receipt_ids": json.loads(row["receipt_ids_json"]),
                "budget_item_id": row["budget_item_id"],
                "settles_budget": bool(row["settles_budget"]),
                "created_by_operation_id": row["created_by_operation_id"],
            }
            for row in db.execute(
                "SELECT entity_id, revision_id, title, amount_cents, currency, paid_on, "
                "category, day, activity_id, notes, receipt_ids_json, budget_item_id, "
                "settles_budget, created_by_operation_id FROM expenses ORDER BY entity_id"
            ).fetchall()
        }
        receipts = {
            row["operation_id"]: {
                "source_device_id": row["source_device_id"],
                "kind": row["kind"],
                "canonical_profile": row["canonical_profile"],
                "intent_sha256": row["intent_sha256"],
                "canonical_intent_json": row["canonical_intent_json"],
                "effect": row["effect"],
                "result_revision_id": row["result_revision_id"],
                "travel_id": row["travel_id"],
                "branch_id": row["branch_id"],
                "entity_id": row["entity_id"],
            }
            for row in db.execute(
                "SELECT operation_id, source_device_id, kind, canonical_profile, "
                "intent_sha256, canonical_intent_json, effect, result_revision_id, "
                "travel_id, branch_id, entity_id FROM operation_receipts ORDER BY operation_id"
            ).fetchall()
        }
        outbox = {
            row["operation_id"]: {
                "source_device_id": row["source_device_id"],
                "kind": row["kind"],
                "canonical_profile": row["canonical_profile"],
                "intent_sha256": row["intent_sha256"],
                "canonical_intent_json": row["canonical_intent_json"],
                "result_revision_id": row["result_revision_id"],
                "state": row["state"],
            }
            for row in db.execute(
                "SELECT operation_id, source_device_id, kind, canonical_profile, "
                "intent_sha256, canonical_intent_json, result_revision_id, state "
                "FROM outbox ORDER BY operation_id"
            ).fetchall()
        }
    return {
        "integrity": integrity,
        "head_revision_id": head,
        "counts": {
            "revisions": len(revisions),
            "expenses": len(expenses),
            "receipts": len(receipts),
            "outbox": len(outbox),
        },
        "revisions": revisions,
        "expenses": expenses,
        "receipts": receipts,
        "outbox": outbox,
    }


def _seed_expenses(db_path: Path, defaults: dict, count: int) -> None:
    if count <= 0:
        return
    rows = [
        (
            defaults["dataset_id"],
            defaults["travel_id"],
            defaults["branch_id"],
            f"seed-expense-{idx}",
            defaults["initial_revision_id"],
            f"预置消费 {idx}",
            100,
            "CNY",
            None,
            "other",
            None,
            None,
            "",
            "[]",
            None,
            0,
            f"seed-op-{idx}",
        )
        for idx in range(count)
    ]
    with closing(sqlite3.connect(db_path)) as db:
        db.executemany(
            "INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        db.commit()


def _classify_actual_outcome(action: str, fault: str | None, raw: dict) -> str:
    """Classify observed desktop return values independently from expected declarations."""
    if fault == "before_commit":
        if raw.get("exit_code") == 86 and raw.get("stdout_empty") is True:
            return "interrupted_before_commit"
        return f"unknown_fault_result:{raw}"
    if fault == "after_commit_before_reply":
        if raw.get("exit_code") == 86 and raw.get("stdout_empty") is True:
            return "interrupted_after_commit"
        return f"unknown_fault_result:{raw}"

    if action in ("lookup", "lookup_under_write_lock"):
        if raw.get("ok") is True:
            state = raw.get("state")
            if state == "committed":
                return "lookup_committed"
            if state == "not_found_at_settled_read":
                return "lookup_not_found_settled"
            if state == "unknown" and raw.get("reason") == "storage_busy":
                return "lookup_unknown_busy"
            return f"unknown_lookup_state:{state!r}:{raw.get('reason')!r}"
        if raw.get("error") == "unauthorized":
            return "rejected_unauthorized"
        return f"unknown_lookup_error:{raw.get('error')!r}"

    if raw.get("ok") is True:
        if raw.get("state") == "committed" and raw.get("replayed") is False:
            return "committed_changed"
        if raw.get("state") == "committed" and raw.get("replayed") is True:
            return "replayed_committed"
        return f"unknown_execute_success:{raw}"

    error = raw.get("error")
    known_errors = {
        "intent_conflict": "rejected_intent_conflict",
        "baseline_conflict": "rejected_baseline_conflict",
        "entity_exists": "rejected_entity_exists",
        "unauthorized": "rejected_unauthorized",
        "expense_limit_exceeded": "rejected_expense_limit",
        "unsupported_capability": "rejected_unsupported_capability",
        "integer_range": "rejected_invalid_input",
        "type": "rejected_invalid_input",
        "invalid_field": "rejected_invalid_input",
    }
    if error in known_errors:
        return known_errors[error]
    return f"unknown_execute_error:{error!r}"


def _build_step_payload(base: dict, patch: dict | None, bindings: dict[str, str]) -> dict:
    doc = _deep_merge(base, patch or {})
    if "expected_revision_var" in doc:
        var_name = doc.pop("expected_revision_var")
        doc["expected_revision_id"] = bindings[var_name]
    return doc


def _build_caller(defaults: dict, override: dict | None) -> CallerContext:
    cfg = {
        "dataset_id": defaults["dataset_id"],
        "device_id": defaults["device_id"],
        "can_write": True,
        "can_lookup_receipt": True,
    }
    if override:
        cfg.update(override)
    return CallerContext.for_fixture(**cfg)


def _check_desktop_raw(expected_raw: dict, actual_raw: dict, bindings: dict[str, str]) -> list[str]:
    mismatches = []
    for key, exp_val in expected_raw.items():
        if key == "result_revision_var":
            actual_rev = (actual_raw.get("receipt") or {}).get("result_revision_id")
            bound = bindings.get(exp_val)
            if actual_rev != bound:
                mismatches.append(f"result_revision_id={actual_rev!r} != bound {exp_val}={bound!r}")
        elif key == "current_revision_var":
            actual_cur = actual_raw.get("current_revision_id")
            bound = bindings.get(exp_val)
            if actual_cur != bound:
                mismatches.append(
                    f"current_revision_id={actual_cur!r} != bound {exp_val}={bound!r}"
                )
        elif key == "effect":
            actual_eff = (actual_raw.get("receipt") or {}).get("effect")
            if actual_eff != exp_val:
                mismatches.append(f"receipt.effect={actual_eff!r} != expected {exp_val!r}")
        else:
            actual_val = actual_raw.get(key)
            if actual_val != exp_val:
                mismatches.append(f"raw.{key}={actual_val!r} != expected {exp_val!r}")

    # Reject leaked receipts or revision IDs on any rejected or non-committed response.
    if actual_raw.get("ok") is False:
        leaked = sorted(k for k in FORBIDDEN_REJECT_KEYS if k in actual_raw)
        if leaked:
            mismatches.append(f"rejected response leaked forbidden keys: {leaked}")
        if actual_raw.get("error") == "unauthorized":
            extra = sorted(actual_raw.keys() - ALLOWED_UNAUTHORIZED_KEYS)
            if extra:
                mismatches.append(f"unauthorized response leaked extra keys: {extra}")
    elif actual_raw.get("state") in ("not_found_at_settled_read", "unknown"):
        if actual_raw.get("receipt") is not None:
            mismatches.append("uncommitted lookup leaked non-null receipt")
        if "result_revision_id" in actual_raw:
            mismatches.append("uncommitted lookup leaked result_revision_id")
    return mismatches


def _check_persisted_content(
    *,
    before: dict,
    after: dict,
    expected_delta: dict,
    input_doc: dict | None,
    raw_input_bytes: bytes | None,
) -> list[str]:
    """Verify actual persisted SQLite rows, not just row counts."""
    mismatches = []
    if all(v == 0 for v in expected_delta.values()):
        for section in ("head_revision_id", "revisions", "expenses", "receipts", "outbox"):
            if after[section] != before[section]:
                mismatches.append(f"persisted {section} mutated on zero-delta step")
        return mismatches

    if expected_delta == {"revisions": 1, "expenses": 1, "receipts": 1, "outbox": 1}:
        assert input_doc is not None and raw_input_bytes is not None
        op = input_doc["operation"]
        target = input_doc["target"]
        payload = input_doc["payload"]
        op_id = op["operation_id"]
        entity_id = target["entity_id"]
        new_rev = after["head_revision_id"]

        # Verify no pre-existing rows were altered.
        for eid, prev_exp in before["expenses"].items():
            if after["expenses"].get(eid) != prev_exp:
                mismatches.append(f"pre-existing expense {eid!r} mutated during new create")
        for oid, prev_rec in before["receipts"].items():
            if after["receipts"].get(oid) != prev_rec:
                mismatches.append(f"pre-existing receipt {oid!r} mutated during new create")
        for oid, prev_out in before["outbox"].items():
            if after["outbox"].get(oid) != prev_out:
                mismatches.append(f"pre-existing outbox {oid!r} mutated during new create")

        # Verify the newly committed expense row field-by-field.
        actual_exp = after["expenses"].get(entity_id)
        expected_exp = {
            "revision_id": new_rev,
            "title": payload["title"],
            "amount_cents": payload["amount_cents"],
            "currency": payload["currency"],
            "paid_on": payload["paid_on"],
            "category": payload["category"],
            "day": payload["day"],
            "activity_id": payload["activity_id"],
            "notes": payload["notes"],
            "receipt_ids": payload["receipt_ids"],
            "budget_item_id": payload["budget_item_id"],
            "settles_budget": bool(payload["settles_budget"]),
            "created_by_operation_id": op_id,
        }
        if actual_exp is None:
            mismatches.append(f"committed expense {entity_id!r} missing from SQLite")
        else:
            for field, exp_val in expected_exp.items():
                if actual_exp.get(field) != exp_val:
                    mismatches.append(
                        f"expense[{entity_id!r}].{field}={actual_exp.get(field)!r} != {exp_val!r}"
                    )

        # Verify new revision row.
        actual_rev = after["revisions"].get(new_rev)
        if actual_rev is None:
            mismatches.append(f"committed revision {new_rev!r} missing from SQLite")
        else:
            if actual_rev["parent_revision_id"] != before["head_revision_id"]:
                mismatches.append(
                    f"revision[{new_rev!r}].parent={actual_rev['parent_revision_id']!r} "
                    f"!= {before['head_revision_id']!r}"
                )
            if actual_rev["source_operation_id"] != op_id:
                mismatches.append(
                    f"revision[{new_rev!r}].source_operation_id="
                    f"{actual_rev['source_operation_id']!r} != {op_id!r}"
                )

        # Verify receipt and outbox rows against canonicalized submitted input.
        _, canonical_bytes, canonical_sha = canonicalize_intent(raw_input_bytes, "control")
        canonical_text = canonical_bytes.decode("utf-8")

        actual_rec = after["receipts"].get(op_id)
        if actual_rec is None:
            mismatches.append(f"committed receipt {op_id!r} missing from SQLite")
        else:
            for field, exp_val in (
                ("source_device_id", op["source_device_id"]),
                ("kind", input_doc["kind"]),
                ("intent_sha256", canonical_sha),
                ("canonical_intent_json", canonical_text),
                ("effect", "changed"),
                ("result_revision_id", new_rev),
                ("travel_id", target["travel_id"]),
                ("branch_id", target["branch_id"]),
                ("entity_id", entity_id),
            ):
                if actual_rec.get(field) != exp_val:
                    mismatches.append(
                        f"receipt[{op_id!r}].{field}={actual_rec.get(field)!r} != {exp_val!r}"
                    )

        actual_out = after["outbox"].get(op_id)
        if actual_out is None:
            mismatches.append(f"committed outbox {op_id!r} missing from SQLite")
        else:
            for field, exp_val in (
                ("source_device_id", op["source_device_id"]),
                ("kind", input_doc["kind"]),
                ("intent_sha256", canonical_sha),
                ("canonical_intent_json", canonical_text),
                ("result_revision_id", new_rev),
                ("state", "pending"),
            ):
                if actual_out.get(field) != exp_val:
                    mismatches.append(
                        f"outbox[{op_id!r}].{field}={actual_out.get(field)!r} != {exp_val!r}"
                    )
    return mismatches


def _measure_input_retention(
    *,
    action: str,
    expected: dict,
    input_doc: dict | None,
    raw_input_bytes: bytes | None,
    after: dict,
) -> tuple[dict, list[str]]:
    """Independently measure input reusability vs candidate requirement."""
    required = bool(expected.get("preserve_input_for_retry"))
    submitted_sha = (
        hashlib.sha256(raw_input_bytes).hexdigest() if raw_input_bytes is not None else None
    )
    mismatches = []
    reusable_canonical_sha = None
    reusable_in_caller = False
    if raw_input_bytes is not None:
        channel = "receipt-query" if action in ("lookup", "lookup_under_write_lock") else "control"
        try:
            _, _, reusable_canonical_sha = canonicalize_intent(raw_input_bytes, channel)
            reusable_in_caller = True
        except Rejected:
            reusable_in_caller = False

    if required and not reusable_in_caller:
        mismatches.append("input marked preserve_input_for_retry is not canonically reusable")

    op_id = (input_doc or {}).get("operation", {}).get("operation_id")
    persisted_in_store = (
        op_id is not None
        and reusable_canonical_sha is not None
        and op_id in after["receipts"]
        and after["receipts"][op_id]["intent_sha256"] == reusable_canonical_sha
        and op_id in after["outbox"]
        and after["outbox"][op_id]["intent_sha256"] == reusable_canonical_sha
    )
    if persisted_in_store:
        note = "verified in SQLite operation_receipts and outbox across reopen"
    elif required:
        note = (
            "unverified across restart: isolated desktop executor has no persistent "
            "pending-intent client store for uncommitted or rejected inputs"
        )
    else:
        note = "not required by candidate step"

    return (
        {
            "required_by_candidate": required,
            "submitted_input_sha256": submitted_sha,
            "reusable_canonical_sha256": reusable_canonical_sha,
            "reusable_in_caller_memory": reusable_in_caller,
            "cross_restart_persistence_verified": persisted_in_store,
            "cross_restart_note": note,
        },
        mismatches,
    )


def load_contract_candidate_spec(bundle_dir: Path = DEFAULT_CONTRACT_BUNDLE) -> dict:
    """Verify candidate contract bundle digests and join normative vectors + desktop assertions."""
    bundle = Path(bundle_dir)
    if not bundle.is_dir():
        raise ValueError(f"Contract candidate directory does not exist: {bundle}")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Contract candidate bundle missing manifest.json.")
    manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    release_key = (manifest.get("contract_id"), manifest.get("contract_version"))
    expected_release = SUPPORTED_CONTRACT_RELEASES.get(release_key)
    if (
        expected_release is None
        or manifest.get("status") != expected_release[0]
        or manifest.get("shared_contract_version") != expected_release[1]
    ):
        raise ValueError("Unsupported or invalid contract bundle manifest.")

    files = manifest.get("files")
    if not isinstance(files, dict) or sorted(files.keys()) != sorted(CONTRACT_CANDIDATE_FILES):
        raise ValueError("Contract candidate manifest file list mismatch.")

    actual_entries = sorted(p.name for p in bundle.iterdir())
    if actual_entries != sorted([*CONTRACT_CANDIDATE_FILES, "manifest.json"]):
        raise ValueError("Contract candidate directory file set mismatch.")

    for name, expected_sha in files.items():
        path = bundle / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Contract candidate missing or invalid file: {name}")
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise ValueError(f"Contract candidate digest mismatch: {name}")

    normative = json.loads((bundle / "normative-vectors.json").read_bytes().decode("utf-8"))
    desktop_assertions = json.loads(
        (bundle / "desktop-assertions.json").read_bytes().decode("utf-8")
    )
    mapping = json.loads((bundle / "result-mapping.json").read_bytes().decode("utf-8"))
    if (
        mapping.get("confirmation_rule", {}).get(
            "minimal_receipt_sufficient_for_intent_confirmation"
        )
        is not False
    ):
        raise ValueError("Contract candidate confirmation rule mismatch.")
    outcomes = mapping.get("outcomes", {})
    if (
        outcomes.get("rejected_unauthorized", {})
        .get("mobile_android", {})
        .get("wrong_target_error")
        != "scope_mismatch"
        or outcomes.get("rejected_unauthorized", {})
        .get("mobile_host", {})
        .get("wrong_target_error")
        != "denied"
        or outcomes.get("lookup_unknown_busy", {}).get("mobile_android", {}).get("state")
        != "pending_busy"
        or outcomes.get("lookup_unknown_busy", {}).get("mobile_host", {}).get("state") != "unknown"
        or outcomes.get("rejected_unsupported_capability", {})
        .get("mobile_android", {})
        .get("error")
        != "unsupported_capability"
        or outcomes.get("rejected_unsupported_capability", {})
        .get("mobile_host", {})
        .get("observed_9dec892", {})
        .get("non_null_paid_on")
        != "committed"
    ):
        raise ValueError("Contract candidate host/Android result-mapping mismatch.")

    canonical_doc = json.loads((bundle / "canonical-digests.json").read_bytes().decode("utf-8"))
    recomputed = {}
    for item in canonical_doc.get("cases", []):
        _, encoded, digest = canonicalize_intent(item["raw_json"].encode("utf-8"), item["channel"])
        if (
            encoded.decode("utf-8") != item["canonical_json"]
            or encoded.hex() != item["canonical_utf8_hex"]
            or digest != item["sha256"]
        ):
            raise ValueError(f"Canonical digest mismatch for {item['id']}.")
        recomputed[item["id"]] = digest
    for rel in canonical_doc.get("relations", []):
        unique = {recomputed[cid] for cid in rel["ids"]}
        expected_size = 1 if rel["relation"] == "same" else len(rel["ids"])
        if len(unique) != expected_size:
            raise ValueError(f"Canonical digest relation failed: {rel['relation']} {rel['ids']}")

    joined_scenarios = []
    for sc in normative["scenarios"]:
        sc_id = sc["id"]
        desk_steps = desktop_assertions["scenarios"].get(sc_id)
        if not isinstance(desk_steps, dict):
            raise ValueError(f"Desktop assertions missing scenario: {sc_id}")
        joined_steps = []
        for st in sc["steps"]:
            st_copy = copy.deepcopy(st)
            if st["name"] not in desk_steps:
                raise ValueError(f"Desktop assertions missing step: {sc_id}/{st['name']}")
            st_copy["expected"]["desktop_raw"] = copy.deepcopy(desk_steps[st["name"]])
            joined_steps.append(st_copy)
        joined_scenarios.append(
            {
                "id": sc_id,
                "title": sc["title"],
                "initial_state": copy.deepcopy(sc["initial_state"]),
                "steps": joined_steps,
            }
        )

    return {
        "format": VECTOR_FORMAT,
        "status": manifest["status"],
        "shared_contract_version": manifest["shared_contract_version"],
        "contract_id": manifest["contract_id"],
        "contract_version": manifest["contract_version"],
        "defaults": copy.deepcopy(normative["defaults"]),
        "scenarios": joined_scenarios,
    }


def verify_formal_matches_candidate(
    formal_dir: Path = DEFAULT_FORMAL_CONTRACT_BUNDLE,
    candidate_dir: Path = DEFAULT_CONTRACT_BUNDLE,
) -> dict:
    """Programmatically verify formal 0.1.0 matches 0.1.0-candidate.2 across all semantic files."""
    formal_spec = load_contract_candidate_spec(formal_dir)
    candidate_spec = load_contract_candidate_spec(candidate_dir)
    if formal_spec["contract_id"] != CONTRACT_FORMAL_ID or (
        formal_spec["contract_version"] != CONTRACT_FORMAL_VERSION
    ):
        raise ValueError("Formal bundle identity mismatch.")
    if formal_spec["defaults"] != candidate_spec["defaults"]:
        raise ValueError("Formal 0.1.0 defaults differ from candidate 2.")
    if formal_spec["scenarios"] != candidate_spec["scenarios"]:
        raise ValueError("Formal 0.1.0 36-step scenarios differ from candidate 2.")

    for name, keys in (
        ("canonical-digests.json", ("wrapper_schema", "cases", "relations")),
        ("result-mapping.json", ("confirmation_rule", "outcomes")),
    ):
        f_doc = json.loads((Path(formal_dir) / name).read_bytes().decode("utf-8"))
        c_doc = json.loads((Path(candidate_dir) / name).read_bytes().decode("utf-8"))
        for key in keys:
            if f_doc[key] != c_doc[key]:
                raise ValueError(f"Formal 0.1.0 {name}[{key}] differs from candidate 2.")

    return {
        "matched": True,
        "formal_version": formal_spec["contract_version"],
        "candidate_version": candidate_spec["contract_version"],
        "scenarios": len(formal_spec["scenarios"]),
    }


def run_contract_candidate(
    bundle_dir: Path = DEFAULT_CONTRACT_BUNDLE,
    *,
    source_fixture_path: Path = DEFAULT_SOURCE_FIXTURE,
) -> dict:
    """Verify a contract candidate bundle and run all scenarios on the desktop executor."""
    spec = load_contract_candidate_spec(bundle_dir)
    return _execute_vector_spec(
        spec,
        source_label=str(bundle_dir),
        source_fixture_path=source_fixture_path,
    )


def run_vectors(
    vectors_path: Path = DEFAULT_VECTORS,
    *,
    source_fixture_path: Path = DEFAULT_SOURCE_FIXTURE,
) -> dict:
    """Execute all candidate vectors against real desktop isolated SQLite entry points."""
    spec = json.loads(vectors_path.read_bytes().decode("utf-8"))
    if spec.get("format") != VECTOR_FORMAT or spec.get("shared_contract_version") is not None:
        raise ValueError("Unsupported or non-candidate vectors file format.")
    return _execute_vector_spec(
        spec,
        source_label=str(vectors_path),
        source_fixture_path=source_fixture_path,
    )


def _execute_vector_spec(
    spec: dict,
    *,
    source_label: str,
    source_fixture_path: Path = DEFAULT_SOURCE_FIXTURE,
) -> dict:
    defaults = spec["defaults"]
    if source_fixture_path.is_file():
        source_fixture = json.loads(source_fixture_path.read_bytes().decode("utf-8"))
        if (
            defaults["base_command"] != source_fixture["command"]
            or defaults["base_receipt_query"] != source_fixture["receipt_query"]
        ):
            raise ValueError("Vector defaults drift from p1_expense_create_candidate.json.")

    scenario_reports = []
    failed_scenarios = []

    for scenario in spec["scenarios"]:
        scenario_id = scenario["id"]
        with tempfile.TemporaryDirectory(prefix=f"p2-vec-{scenario_id}-") as tmp:
            folder = Path(tmp).resolve()
            db_path = folder / "isolated.sqlite"
            init_isolated_store(
                db_path,
                dataset_id=defaults["dataset_id"],
                travel_id=defaults["travel_id"],
                branch_id=defaults["branch_id"],
                initial_revision_id=defaults["initial_revision_id"],
            )
            _seed_expenses(db_path, defaults, scenario["initial_state"].get("seeded_expenses", 0))

            bindings: dict[str, str] = {}
            step_reports = []
            scenario_failures = []

            for step in scenario["steps"]:
                step_name = step["name"]
                action = step["action"]
                fault = step.get("fault")
                expected = step["expected"]
                caller = _build_caller(defaults, step.get("caller"))

                before = _snapshot_store(db_path)
                raw_input_bytes: bytes | None = None
                input_doc: dict | None = None

                if action == "execute":
                    input_doc = _build_step_payload(
                        defaults["base_command"], step.get("patch"), bindings
                    )
                    raw_input_bytes = json.dumps(
                        input_doc,
                        ensure_ascii=False,
                        sort_keys=bool(step.get("sort_keys")),
                    ).encode("utf-8")
                    if fault is not None:
                        cmd_file = folder / f"{step_name}.json"
                        cmd_file.write_bytes(raw_input_bytes)
                        proc = subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "desktop.expense_executor",
                                "execute",
                                "--database",
                                str(db_path),
                                "--input",
                                str(cmd_file),
                                "--dataset-id",
                                caller.dataset_id,
                                "--device-id",
                                caller.device_id,
                                "--fault",
                                fault,
                            ],
                            cwd=REPO_ROOT,
                            capture_output=True,
                            timeout=10,
                            check=False,
                        )
                        actual_raw = {
                            "exit_code": proc.returncode,
                            "stdout_empty": proc.stdout == b"",
                        }
                    else:
                        actual_raw = execute_command(db_path, raw_input_bytes, caller=caller)
                elif action in ("lookup", "lookup_under_write_lock"):
                    input_doc = _build_step_payload(
                        defaults["base_receipt_query"], step.get("patch"), bindings
                    )
                    raw_input_bytes = json.dumps(input_doc, ensure_ascii=False).encode("utf-8")
                    if action == "lookup_under_write_lock":
                        with closing(
                            sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
                        ) as lock_conn:
                            lock_conn.execute("BEGIN IMMEDIATE")
                            try:
                                actual_raw = lookup_receipt(db_path, raw_input_bytes, caller=caller)
                            finally:
                                lock_conn.execute("ROLLBACK")
                    else:
                        actual_raw = lookup_receipt(db_path, raw_input_bytes, caller=caller)
                else:
                    raise ValueError(f"Unknown vector step action: {action!r}")

                after = _snapshot_store(db_path)

                if "bind_revision" in step:
                    rev_id = (actual_raw.get("receipt") or {}).get("result_revision_id")
                    if rev_id:
                        bindings[step["bind_revision"]] = rev_id
                if "bind_revision_from_head" in step:
                    bindings[step["bind_revision_from_head"]] = after["head_revision_id"]

                observed_outcome = _classify_actual_outcome(action, fault, actual_raw)
                actual_delta = {
                    key: after["counts"][key] - before["counts"][key]
                    for key in ("revisions", "expenses", "receipts", "outbox")
                }

                step_mismatches = []
                if after["integrity"] != [("ok",)]:
                    step_mismatches.append(f"SQLite integrity failed: {after['integrity']!r}")
                if observed_outcome != expected["business_outcome"]:
                    step_mismatches.append(
                        f"business_outcome={observed_outcome!r} != "
                        f"expected {expected['business_outcome']!r}"
                    )
                step_mismatches.extend(
                    _check_desktop_raw(expected["desktop_raw"], actual_raw, bindings)
                )
                if actual_delta != expected["state_delta"]:
                    step_mismatches.append(
                        f"state_delta={actual_delta!r} != expected {expected['state_delta']!r}"
                    )
                if "head_revision_var" in expected:
                    exp_head = bindings.get(expected["head_revision_var"])
                    if after["head_revision_id"] != exp_head:
                        step_mismatches.append(
                            f"head={after['head_revision_id']!r} != "
                            f"bound {expected['head_revision_var']}={exp_head!r}"
                        )
                if (
                    "head_revision_literal" in expected
                    and after["head_revision_id"] != expected["head_revision_literal"]
                ):
                    step_mismatches.append(
                        f"head={after['head_revision_id']!r} != "
                        f"expected {expected['head_revision_literal']!r}"
                    )

                step_mismatches.extend(
                    _check_persisted_content(
                        before=before,
                        after=after,
                        expected_delta=expected["state_delta"],
                        input_doc=input_doc,
                        raw_input_bytes=raw_input_bytes,
                    )
                )

                retention_report, retention_mismatches = _measure_input_retention(
                    action=action,
                    expected=expected,
                    input_doc=input_doc,
                    raw_input_bytes=raw_input_bytes,
                    after=after,
                )
                step_mismatches.extend(retention_mismatches)

                stored_intent_sha = None
                if input_doc is not None:
                    op_id = input_doc["operation"]["operation_id"]
                    if op_id in after["receipts"]:
                        stored_intent_sha = after["receipts"][op_id]["intent_sha256"]

                if step_mismatches:
                    scenario_failures.append({"step": step_name, "mismatches": step_mismatches})

                step_reports.append(
                    {
                        "step": step_name,
                        "action": action,
                        "fault": fault,
                        "observed_outcome": observed_outcome,
                        "desktop_raw": actual_raw,
                        "counts": after["counts"],
                        "delta": actual_delta,
                        "head_revision_id": after["head_revision_id"],
                        "stored_intent_sha256": stored_intent_sha,
                        "input_retention": retention_report,
                        "passed": not step_mismatches,
                        "mismatches": step_mismatches,
                    }
                )

            passed = not scenario_failures
            if not passed:
                failed_scenarios.append(scenario_id)
            scenario_reports.append(
                {
                    "id": scenario_id,
                    "title": scenario["title"],
                    "passed": passed,
                    "bindings": bindings,
                    "steps": step_reports,
                    "failures": scenario_failures,
                }
            )

    return {
        "format": spec["format"],
        "status": spec["status"],
        "shared_contract_version": spec["shared_contract_version"],
        "vectors_path": source_label,
        "total_scenarios": len(scenario_reports),
        "passed_scenarios": len(scenario_reports) - len(failed_scenarios),
        "failed_scenarios": failed_scenarios,
        "all_passed": not failed_scenarios,
        "scenarios": scenario_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path, default=DEFAULT_VECTORS)
    parser.add_argument("--contract-bundle", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.contract_bundle is not None:
        report = run_contract_candidate(args.contract_bundle)
    else:
        report = run_vectors(args.vectors)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for sc in report["scenarios"]:
            mark = "PASS" if sc["passed"] else "FAIL"
            last = sc["steps"][-1]
            print(
                f"[{mark}] {sc['id']} ({len(sc['steps'])} steps) "
                f"counts={last['counts']} bindings={sc['bindings']}"
            )
            for fail in sc["failures"]:
                print(f"  step={fail['step']}: {'; '.join(fail['mismatches'])}")
        print(
            f"Summary: {report['passed_scenarios']}/{report['total_scenarios']} scenarios passed."
        )
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
