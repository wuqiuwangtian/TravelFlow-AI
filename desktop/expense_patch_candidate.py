"""Isolated SQLite reference executor & runner for expense-patch-candidate 0.1.0-candidate.1."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_DIR = REPO_ROOT / "contracts" / "expense-patch-candidate"

CONTRACT_ID = "travelflow.expense-patch-candidate"
CONTRACT_VERSION = "0.1.0-candidate.1"
CANONICAL_PROFILE = "p1-host-intent-c14n-1"
CONTROL_PROFILE = "p1-host-control-1"
QUERY_PROFILE = "p1-host-query-1"
SAFE_INTEGER = 2**53 - 1
MAX_CANONICAL_BYTES = 65536

OPERATION_KEYS = frozenset({"dataset_id", "source_device_id", "operation_id"})
TARGET_KEYS = frozenset({"travel_id", "branch_id", "entity_id"})
SUPPORTED_PATCH_FIELDS = frozenset({"amount_cents"})
KNOWN_OUT_OF_SLICE_PATCH_FIELDS = frozenset(
    {
        "title",
        "notes",
        "currency",
        "paid_on",
        "category",
        "day",
        "activity_id",
        "receipt_ids",
        "allow_shared_receipts",
        "budget_item_id",
        "settles_budget",
    }
)
REQUIRED_BUNDLE_FILES = frozenset(
    {
        "SPEC.md",
        "canonical-digests.json",
        "normative-vectors.json",
        "result-mapping.json",
    }
)
NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


class PatchRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PatchScope:
    dataset_id: str
    authorized_devices: frozenset[str]
    authorized_targets: frozenset[tuple[str, str]]
    can_write: bool = True
    can_lookup_receipt: bool = True


class _TokenReader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def _ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _take(self, expected: str) -> None:
        self._ws()
        if not self.text.startswith(expected, self.pos):
            raise PatchRejected("json")
        self.pos += len(expected)

    def _string(self) -> str:
        self._ws()
        if not self.text.startswith('"', self.pos):
            raise PatchRejected("json")
        try:
            scan = json.decoder.scanstring  # type: ignore[attr-defined]
            val, self.pos = scan(self.text, self.pos + 1, True)
            return str(val)
        except (ValueError, json.JSONDecodeError):
            raise PatchRejected("json") from None

    def _value(self) -> Any:
        self._ws()
        if self.pos == len(self.text):
            raise PatchRejected("json")
        ch = self.text[self.pos]
        if ch == '"':
            return ["string", self._string()]
        if ch == "{":
            self.pos += 1
            pairs = []
            self._ws()
            if not self.text.startswith("}", self.pos):
                while True:
                    key = self._string()
                    self._take(":")
                    pairs.append([key, self._value()])
                    self._ws()
                    if self.text.startswith("}", self.pos):
                        break
                    self._take(",")
            self._take("}")
            return ["object", pairs]
        if ch == "[":
            self.pos += 1
            items = []
            self._ws()
            if not self.text.startswith("]", self.pos):
                while True:
                    items.append(self._value())
                    self._ws()
                    if self.text.startswith("]", self.pos):
                        break
                    self._take(",")
            self._take("]")
            return ["array", items]
        for spelling, res in (
            ("true", ["boolean", True]),
            ("false", ["boolean", False]),
            ("null", ["null"]),
        ):
            if self.text.startswith(spelling, self.pos):
                self.pos += len(spelling)
                return res
        m = NUMBER_RE.match(self.text, self.pos)
        if m:
            self.pos = m.end()
            return ["number", m[0]]
        raise PatchRejected("json")

    def document(self) -> Any:
        tree = self._value()
        self._ws()
        if self.pos != len(self.text):
            raise PatchRejected("json")
        return tree


def _require(cond: bool, code: str) -> None:
    if not cond:
        raise PatchRejected(code)


def _check_scalar(s: str) -> None:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in s):
        raise PatchRejected("unicode")


def _check_tree(node: Any) -> None:
    tag = node[0]
    if tag == "string":
        _check_scalar(node[1])
    elif tag == "array":
        for item in node[1]:
            _check_tree(item)
    elif tag == "object":
        seen: set[str] = set()
        for k, v in node[1]:
            _check_scalar(k)
            if k in seen:
                raise PatchRejected("duplicate_key")
            seen.add(k)
            _check_tree(v)


def _parse_exact_int(token: str) -> int:
    _require(len(token) <= 64, "resource_number")
    mantissa, _, exp = token.lower().partition("e")
    whole, _, frac = mantissa.lstrip("-").partition(".")
    coeff = int(whole + frac)
    if coeff == 0:
        return 0
    power = int(exp or "0") - len(frac)
    _require(1 <= len(str(coeff)) + power <= 16, "integer_range")
    if power < 0:
        coeff, rem = divmod(coeff, 10**-power)
        _require(rem == 0, "integer_range")
    else:
        coeff *= 10**power
    _require(coeff <= SAFE_INTEGER, "integer_range")
    return -coeff if mantissa.startswith("-") else coeff


def _read_canonical(node: Any) -> Any:
    tag = node[0]
    if tag == "object":
        pairs = sorted(node[1], key=lambda p: p[0].encode("utf-16-be"))
        return {k: _read_canonical(v) for k, v in pairs}
    if tag == "array":
        return [_read_canonical(v) for v in node[1]]
    if tag == "number":
        return _parse_exact_int(node[1])
    return None if tag == "null" else node[1]


def _check_ids(obj: Any, keys: frozenset[str]) -> None:
    _require(isinstance(obj, dict) and obj.keys() == keys, "shape")
    for val in obj.values():
        _require(
            isinstance(val, str) and 1 <= len(val) <= 256 and len(val.encode()) <= 1024, "identity"
        )


def canonicalize_patch_intent(raw: bytes, channel: str) -> tuple[dict[str, Any], bytes, str]:
    _require(channel in ("control", "receipt-query"), "canonical_channel")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PatchRejected("encoding") from None
    tree = _TokenReader(text).document()
    _check_tree(tree)
    value = _read_canonical(tree)

    is_query = channel == "receipt-query"
    base_keys = frozenset({"profile", "kind", "operation"})
    expected_keys = (
        base_keys
        if is_query
        else base_keys
        | frozenset({"target", "expected_revision_id", "source_revision_id", "patch"})
    )
    _require(isinstance(value, dict) and value.keys() == expected_keys, "shape")
    _check_ids(value["operation"], OPERATION_KEYS)
    _require(
        value["profile"] == (QUERY_PROFILE if is_query else CONTROL_PROFILE),
        "unsupported_profile",
    )
    if is_query:
        _require(value["kind"] == "receipt.lookup", "unsupported_command")
    else:
        _require(value["kind"] == "expense.patch", "unsupported_command")
        _check_ids(value["target"], TARGET_KEYS)
        _check_ids(
            {
                "expected": value["expected_revision_id"],
                "source": value["source_revision_id"],
            },
            frozenset({"expected", "source"}),
        )
        _require(isinstance(value["patch"], dict), "shape")

    wrapped = {"canonical_profile": CANONICAL_PROFILE, "channel": channel, "intent": value}
    encoded = json.dumps(wrapped, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _require(len(encoded) <= MAX_CANONICAL_BYTES, "canonical_bytes")
    return value, encoded, hashlib.sha256(encoded).hexdigest()


REPLICA_SCHEMA = """
CREATE TABLE branch_state (
    dataset_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    local_revision_id TEXT NOT NULL,
    synced_desktop_revision_id TEXT NOT NULL,
    local_seq INTEGER NOT NULL,
    PRIMARY KEY (dataset_id, travel_id, branch_id)
);

CREATE TABLE expenses (
    dataset_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    title TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    paid_on TEXT,
    category TEXT NOT NULL,
    notes TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, travel_id, branch_id, entity_id)
);

CREATE TABLE pending_operations (
    dataset_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    delivery_state TEXT NOT NULL,
    raw_intent_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, travel_id, branch_id, operation_id)
);

CREATE TABLE received_receipts (
    dataset_id TEXT NOT NULL,
    source_device_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    canonical_profile TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    canonical_intent_json TEXT NOT NULL,
    effect TEXT NOT NULL,
    result_revision_id TEXT NOT NULL,
    synced_desktop_revision_id TEXT NOT NULL,
    travel_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    origin_direction TEXT NOT NULL DEFAULT 'received',
    committed_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, source_device_id, operation_id)
);
"""


def init_mobile_replica(
    db_path: Path,
    *,
    defaults: dict[str, Any],
    expense_override: dict[str, Any] | None = None,
    initial_pending: list[dict[str, Any]] | None = None,
) -> None:
    exp = dict(defaults["initial_expense"])
    if expense_override:
        exp.update(expense_override)
    with closing(sqlite3.connect(db_path, isolation_level=None)) as db:
        db.executescript(REPLICA_SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO branch_state VALUES (?, ?, ?, ?, ?, 1)",
            (
                defaults["dataset_id"],
                defaults["travel_id"],
                defaults["branch_id"],
                defaults["initial_local_revision_id"],
                defaults["initial_synced_desktop_revision_id"],
            ),
        )
        db.execute(
            "INSERT INTO expenses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                defaults["dataset_id"],
                defaults["travel_id"],
                defaults["branch_id"],
                exp["id"],
                exp["title"],
                exp["amount_cents"],
                exp["currency"],
                exp["paid_on"],
                exp["category"],
                exp["notes"],
                exp["updated_at"],
            ),
        )
        for item in initial_pending or []:
            db.execute(
                "INSERT INTO pending_operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    defaults["dataset_id"],
                    defaults["travel_id"],
                    defaults["branch_id"],
                    item["operation_id"],
                    item["entity_id"],
                    item["delivery_state"],
                    item["raw_intent_json"],
                ),
            )
        db.execute("COMMIT")


def _validate_patch_payload(patch: dict[str, Any]) -> None:
    for key in patch:
        if key in KNOWN_OUT_OF_SLICE_PATCH_FIELDS:
            raise PatchRejected("unsupported_capability")
        if key not in SUPPORTED_PATCH_FIELDS:
            raise PatchRejected("invalid_field")
    if "amount_cents" in patch:
        amt = patch["amount_cents"]
        _require(type(amt) is int, "type")
        _require(1 <= amt <= 9_999_999_999, "integer_range")


def _authorize(
    scope: PatchScope,
    op: dict[str, Any],
    *,
    require_write: bool,
    target: dict[str, Any] | None = None,
) -> None:
    if require_write and not scope.can_write:
        raise PatchRejected("unauthorized")
    if not require_write and not scope.can_lookup_receipt:
        raise PatchRejected("unauthorized")
    if op["dataset_id"] != scope.dataset_id:
        raise PatchRejected("unauthorized")
    if op["source_device_id"] not in scope.authorized_devices:
        raise PatchRejected("unauthorized")
    if target is not None and (target["travel_id"], target["branch_id"]) not in (
        scope.authorized_targets
    ):
        raise PatchRejected("unauthorized")


def _format_receipt(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "operation": {
            "dataset_id": row["dataset_id"],
            "source_device_id": row["source_device_id"],
            "operation_id": row["operation_id"],
        },
        "kind": row["kind"],
        "canonical_profile": row["canonical_profile"],
        "intent_sha256": row["intent_sha256"],
        "effect": row["effect"],
        "result_revision_id": row["result_revision_id"],
        "synced_desktop_revision_id": row["synced_desktop_revision_id"],
        "travel_id": row["travel_id"],
        "branch_id": row["branch_id"],
        "entity_id": row["entity_id"],
        "origin_direction": row["origin_direction"],
    }


def apply_desktop_patch(db_path: Path, raw_command: bytes, *, scope: PatchScope) -> dict[str, Any]:
    """Reference mobile receiver for travelflow.expense-patch-candidate 0.1.0-candidate.1."""
    try:
        if not scope.can_write:
            raise PatchRejected("unauthorized")
        cmd, canonical_bytes, digest = canonicalize_patch_intent(raw_command, "control")
        op = cmd["operation"]
        target = cmd["target"]
        _authorize(scope, op, require_write=True, target=target)
    except PatchRejected as err:
        return {"ok": False, "error": err.code}

    canonical_text = canonical_bytes.decode("utf-8")
    with closing(sqlite3.connect(db_path, isolation_level=None)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        try:
            # 1. Durable receipt check first (idempotent replay / intent conflict).
            existing = db.execute(
                "SELECT * FROM received_receipts "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (op["dataset_id"], op["source_device_id"], op["operation_id"]),
            ).fetchone()
            if existing is not None:
                db.execute("ROLLBACK")
                if (
                    existing["intent_sha256"] == digest
                    and existing["canonical_intent_json"] == canonical_text
                ):
                    return {
                        "ok": True,
                        "replayed": True,
                        "state": "committed",
                        "receipt": _format_receipt(existing),
                    }
                return {"ok": False, "error": "intent_conflict"}

            # 2. Validate patch capability & field ranges.
            try:
                _validate_patch_payload(cmd["patch"])
            except PatchRejected as err:
                db.execute("ROLLBACK")
                return {"ok": False, "error": err.code}

            # 3. Local pending outbox protection: preserve unconfirmed local edits fail-closed.
            blocking = db.execute(
                "SELECT operation_id, entity_id, delivery_state FROM pending_operations "
                "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ? "
                "AND delivery_state != 'confirmed' LIMIT 1",
                (op["dataset_id"], target["travel_id"], target["branch_id"]),
            ).fetchone()
            if blocking is not None:
                db.execute("ROLLBACK")
                return {
                    "ok": False,
                    "error": "local_pending_conflict",
                    "blocking_operation_id": blocking["operation_id"],
                    "blocking_entity_id": blocking["entity_id"],
                }

            # 4. Baseline check against synced_desktop_revision_id (NOT local_revision_id).
            branch = db.execute(
                "SELECT local_revision_id, synced_desktop_revision_id, local_seq "
                "FROM branch_state WHERE dataset_id = ? AND travel_id = ? AND branch_id = ?",
                (op["dataset_id"], target["travel_id"], target["branch_id"]),
            ).fetchone()
            if branch is None:
                db.execute("ROLLBACK")
                return {"ok": False, "error": "unauthorized"}
            if branch["synced_desktop_revision_id"] != cmd["expected_revision_id"]:
                db.execute("ROLLBACK")
                return {
                    "ok": False,
                    "error": "baseline_conflict",
                    "current_synced_revision_id": branch["synced_desktop_revision_id"],
                }

            # 5. Entity existence and CNY-only check.
            exp = db.execute(
                "SELECT * FROM expenses "
                "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ? AND entity_id = ?",
                (op["dataset_id"], target["travel_id"], target["branch_id"], target["entity_id"]),
            ).fetchone()
            if exp is None:
                db.execute("ROLLBACK")
                return {"ok": False, "error": "entity_not_found"}
            if exp["currency"] != "CNY":
                db.execute("ROLLBACK")
                return {"ok": False, "error": "unsupported_capability"}

            # 6. Apply partial patch (reusing eee6cda7 no-change rule).
            new_amount = cmd["patch"].get("amount_cents", exp["amount_cents"])
            changed = new_amount != exp["amount_cents"]
            now_utc = datetime.now(UTC).isoformat()

            if changed:
                next_seq = int(branch["local_seq"]) + 1
                result_rev = f"m-rev-{next_seq}"
                synced_rev = cmd["source_revision_id"]
                effect = "changed"
                db.execute(
                    "UPDATE expenses SET amount_cents = ?, updated_at = ? "
                    "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ? AND entity_id = ?",
                    (
                        new_amount,
                        now_utc,
                        op["dataset_id"],
                        target["travel_id"],
                        target["branch_id"],
                        target["entity_id"],
                    ),
                )
                db.execute(
                    "UPDATE branch_state SET local_revision_id = ?, "
                    "synced_desktop_revision_id = ?, local_seq = ? "
                    "WHERE dataset_id = ? AND travel_id = ? AND branch_id = ?",
                    (
                        result_rev,
                        synced_rev,
                        next_seq,
                        op["dataset_id"],
                        target["travel_id"],
                        target["branch_id"],
                    ),
                )
            else:
                result_rev = str(branch["local_revision_id"])
                synced_rev = str(branch["synced_desktop_revision_id"])
                effect = "no_change"

            db.execute(
                "INSERT INTO received_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'received', ?)",
                (
                    op["dataset_id"],
                    op["source_device_id"],
                    op["operation_id"],
                    cmd["kind"],
                    CANONICAL_PROFILE,
                    digest,
                    canonical_text,
                    effect,
                    result_rev,
                    synced_rev,
                    target["travel_id"],
                    target["branch_id"],
                    target["entity_id"],
                    now_utc,
                ),
            )
            row = db.execute(
                "SELECT * FROM received_receipts "
                "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
                (op["dataset_id"], op["source_device_id"], op["operation_id"]),
            ).fetchone()
            db.execute("COMMIT")
            return {
                "ok": True,
                "replayed": False,
                "state": "committed",
                "receipt": _format_receipt(row),
            }
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise


def lookup_patch_receipt(db_path: Path, raw_query: bytes, *, scope: PatchScope) -> dict[str, Any]:
    try:
        if not scope.can_lookup_receipt:
            raise PatchRejected("unauthorized")
        query, _, _ = canonicalize_patch_intent(raw_query, "receipt-query")
        op = query["operation"]
        _authorize(scope, op, require_write=False)
    except PatchRejected as err:
        return {"ok": False, "error": err.code}

    with closing(sqlite3.connect(db_path, isolation_level=None)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM received_receipts "
            "WHERE dataset_id = ? AND source_device_id = ? AND operation_id = ?",
            (op["dataset_id"], op["source_device_id"], op["operation_id"]),
        ).fetchone()
    if row is None:
        return {"ok": True, "state": "not_found_at_settled_read", "receipt": None}
    if (row["travel_id"], row["branch_id"]) not in scope.authorized_targets:
        return {"ok": False, "error": "unauthorized"}
    return {"ok": True, "state": "committed", "receipt": _format_receipt(row)}


def _classify_outcome(raw: dict[str, Any], action: str) -> str:
    if action == "lookup":
        if not raw.get("ok"):
            return (
                "rejected_unauthorized"
                if raw.get("error") == "unauthorized"
                else "rejected_invalid_input"
            )
        if raw.get("state") == "committed":
            return "lookup_committed"
        return "lookup_not_found_settled"
    if raw.get("ok"):
        if raw.get("replayed"):
            return "replayed_committed"
        effect = (raw.get("receipt") or {}).get("effect")
        return "committed_changed" if effect == "changed" else "committed_no_change"
    err = raw.get("error")
    return {
        "intent_conflict": "rejected_intent_conflict",
        "local_pending_conflict": "rejected_local_pending_conflict",
        "baseline_conflict": "rejected_baseline_conflict",
        "entity_not_found": "rejected_entity_not_found",
        "unsupported_capability": "rejected_unsupported_capability",
        "unauthorized": "rejected_unauthorized",
    }.get(str(err), "rejected_invalid_input")


def verify_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Verify manifest digests, canonical digests, and all 9 normative scenarios."""
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise AssertionError(f"Missing manifest.json in {bundle_dir}")
    manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    if (
        manifest.get("contract_id") != CONTRACT_ID
        or manifest.get("contract_version") != CONTRACT_VERSION
    ):
        raise AssertionError("Manifest contract_id / contract_version mismatch")
    if set(manifest.get("files", {}).keys()) != REQUIRED_BUNDLE_FILES:
        raise AssertionError("Manifest file list mismatch")

    for name, expected_sha in manifest["files"].items():
        fp = bundle_dir / name
        if not fp.is_file():
            raise AssertionError(f"Missing bundle file: {name}")
        actual_sha = hashlib.sha256(fp.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise AssertionError(f"Digest mismatch for {name}: {actual_sha} != {expected_sha}")

    digests_doc = json.loads((bundle_dir / "canonical-digests.json").read_bytes().decode("utf-8"))
    computed: dict[str, str] = {}
    for case in digests_doc["cases"]:
        _, encoded, sha = canonicalize_patch_intent(
            case["raw_json"].encode("utf-8"), case["channel"]
        )
        if encoded.decode("utf-8") != case["canonical_json"]:
            raise AssertionError(f"Canonical JSON mismatch in {case['id']}")
        if encoded.hex() != case["canonical_utf8_hex"]:
            raise AssertionError(f"Canonical hex mismatch in {case['id']}")
        if sha != case["sha256"]:
            raise AssertionError(f"Canonical SHA-256 mismatch in {case['id']}")
        computed[case["id"]] = sha
    for rel in digests_doc["relations"]:
        unique = {computed[cid] for cid in rel["ids"]}
        if rel["relation"] == "same" and len(unique) != 1:
            raise AssertionError(f"Expected identical digest for {rel['ids']}")
        if rel["relation"] == "distinct" and len(unique) != len(rel["ids"]):
            raise AssertionError(f"Expected distinct digests for {rel['ids']}")

    vectors = json.loads((bundle_dir / "normative-vectors.json").read_bytes().decode("utf-8"))
    defaults = vectors["defaults"]
    total_steps = 0

    with tempfile.TemporaryDirectory(prefix="p3-patch-candidate-") as tmp:
        for sc in vectors["scenarios"]:
            db_path = Path(tmp) / f"{sc['id']}.sqlite"
            init_mobile_replica(
                db_path,
                defaults=defaults,
                expense_override=sc.get("initial_expense_override"),
                initial_pending=sc.get("initial_pending"),
            )
            bindings: dict[str, str] = {}
            for step in sc["steps"]:
                total_steps += 1
                ov = step.get("scope_override") or {}
                scope = PatchScope(
                    dataset_id=defaults["dataset_id"],
                    authorized_devices=frozenset({defaults["desktop_device_id"]}),
                    authorized_targets=frozenset({(defaults["travel_id"], defaults["branch_id"])}),
                    can_write=ov.get("can_write", True),
                    can_lookup_receipt=ov.get("can_lookup_receipt", True),
                )
                if step["action"] == "patch":
                    raw_in = json.dumps(step["command"], ensure_ascii=False).encode("utf-8")
                    res = apply_desktop_patch(db_path, raw_in, scope=scope)
                else:
                    raw_in = json.dumps(step["query"], ensure_ascii=False).encode("utf-8")
                    res = lookup_patch_receipt(db_path, raw_in, scope=scope)

                actual_outcome = _classify_outcome(res, step["action"])
                exp = step["expected"]
                if actual_outcome != exp["business_outcome"]:
                    raise AssertionError(
                        f"{sc['id']}/{step['name']}: expected {exp['business_outcome']}, "
                        f"got {actual_outcome} (raw={res})"
                    )
                if "bind_local_revision" in step:
                    bindings[step["bind_local_revision"]] = res["receipt"]["result_revision_id"]
                if "result_revision_var" in exp:
                    want_rev = bindings[exp["result_revision_var"]]
                    got_rev = (res.get("receipt") or {}).get("result_revision_id")
                    if got_rev != want_rev:
                        raise AssertionError(
                            f"{sc['id']}/{step['name']}: revision {got_rev} != {want_rev}"
                        )

                with closing(sqlite3.connect(db_path)) as db:
                    db.row_factory = sqlite3.Row
                    e_row = db.execute(
                        "SELECT * FROM expenses WHERE entity_id = ?", (defaults["entity_id"],)
                    ).fetchone()
                    b_row = db.execute("SELECT * FROM branch_state").fetchone()
                    r_cnt = db.execute("SELECT COUNT(*) FROM received_receipts").fetchone()[0]
                    p_cnt = db.execute("SELECT COUNT(*) FROM pending_operations").fetchone()[0]

                if "amount_cents" in exp and e_row["amount_cents"] != exp["amount_cents"]:
                    raise AssertionError(
                        f"{sc['id']}/{step['name']}: amount {e_row['amount_cents']} "
                        f"!= {exp['amount_cents']}"
                    )
                if (
                    "synced_desktop_revision_id" in exp
                    and b_row["synced_desktop_revision_id"] != exp["synced_desktop_revision_id"]
                ):
                    raise AssertionError(
                        f"{sc['id']}/{step['name']}: synced_desktop_revision_id mismatch"
                    )
                if (
                    "local_revision_id" in exp
                    and b_row["local_revision_id"] != exp["local_revision_id"]
                ):
                    raise AssertionError(f"{sc['id']}/{step['name']}: local_revision_id mismatch")
                if "updated_at" in exp and e_row["updated_at"] != exp["updated_at"]:
                    raise AssertionError(
                        f"{sc['id']}/{step['name']}: updated_at mutated unexpectedly"
                    )
                if "receipt_count" in exp and r_cnt != exp["receipt_count"]:
                    raise AssertionError(
                        f"{sc['id']}/{step['name']}: receipts {r_cnt} != {exp['receipt_count']}"
                    )
                if "pending_count" in exp and p_cnt != exp["pending_count"]:
                    raise AssertionError(
                        f"{sc['id']}/{step['name']}: pending {p_cnt} != {exp['pending_count']}"
                    )
                for field, want_val in (exp.get("preserved_fields") or {}).items():
                    if e_row[field] != want_val:
                        raise AssertionError(
                            f"{sc['id']}/{step['name']}: field {field} expected {want_val!r}, "
                            f"got {e_row[field]!r}"
                        )

    return {
        "ok": True,
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "scenarios": len(vectors["scenarios"]),
        "steps": total_steps,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-bundle", type=Path, default=DEFAULT_BUNDLE_DIR)
    args = parser.parse_args(argv)
    try:
        summary = verify_bundle(args.contract_bundle)
    except AssertionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
