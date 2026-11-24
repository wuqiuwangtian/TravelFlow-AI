"""Desktop checker for portable Phase 2 expense.create acceptance vectors."""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from desktop.expense_executor import (
    CallerContext,
    canonicalize_intent,
    execute_command,
    init_isolated_store,
    lookup_receipt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VECTORS = REPO_ROOT / "backend" / "tests" / "fixtures" / "p1_expense_create_vectors.json"
DEFAULT_SOURCE_FIXTURE = (
    REPO_ROOT / "backend" / "tests" / "fixtures" / "p1_expense_create_candidate.json"
)
VECTOR_FORMAT = "travelflow-expense-create-vectors-candidate/0-draft"


def _deep_merge(base: dict, patch: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _snapshot_store(db_path: Path) -> dict:
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        integrity = [tuple(row) for row in db.execute("PRAGMA integrity_check").fetchall()]
        head = db.execute("SELECT head_revision_id FROM branches LIMIT 1").fetchone()[0]
        revisions = db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        expenses = db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        receipts = db.execute("SELECT COUNT(*) FROM operation_receipts").fetchone()[0]
        outbox = db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        stored_intents = {
            row["operation_id"]: {
                "intent_sha256": row["intent_sha256"],
                "canonical_intent_json": row["canonical_intent_json"],
                "result_revision_id": row["result_revision_id"],
            }
            for row in db.execute(
                "SELECT operation_id, intent_sha256, canonical_intent_json, result_revision_id "
                "FROM operation_receipts"
            ).fetchall()
        }
    return {
        "integrity": integrity,
        "head_revision_id": head,
        "counts": {
            "revisions": revisions,
            "expenses": expenses,
            "receipts": receipts,
            "outbox": outbox,
        },
        "stored_intents": stored_intents,
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
    return mismatches


def run_vectors(
    vectors_path: Path = DEFAULT_VECTORS,
    *,
    source_fixture_path: Path = DEFAULT_SOURCE_FIXTURE,
) -> dict:
    """Execute all candidate vectors against real desktop isolated SQLite entry points."""
    spec = json.loads(vectors_path.read_bytes().decode("utf-8"))
    if spec.get("format") != VECTOR_FORMAT or spec.get("shared_contract_version") is not None:
        raise ValueError("Unsupported or non-candidate vectors file format.")

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

                stored_intent_sha = None
                if expected.get("check_stored_intent") and raw_input_bytes is not None:
                    _, canonical_bytes, canonical_sha = canonicalize_intent(
                        raw_input_bytes, "control"
                    )
                    op_id = input_doc["operation"]["operation_id"]
                    stored = after["stored_intents"].get(op_id)
                    if stored is None:
                        step_mismatches.append(f"missing stored receipt intent for {op_id}")
                    else:
                        stored_intent_sha = stored["intent_sha256"]
                        if stored["intent_sha256"] != canonical_sha or stored[
                            "canonical_intent_json"
                        ] != canonical_bytes.decode("utf-8"):
                            step_mismatches.append(f"stored intent mismatch for {op_id}")

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
                        "preserve_input_for_retry": bool(expected.get("preserve_input_for_retry")),
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
        "vectors_path": str(vectors_path),
        "total_scenarios": len(scenario_reports),
        "passed_scenarios": len(scenario_reports) - len(failed_scenarios),
        "failed_scenarios": failed_scenarios,
        "all_passed": not failed_scenarios,
        "scenarios": scenario_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path, default=DEFAULT_VECTORS)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

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
