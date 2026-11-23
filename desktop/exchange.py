"""Read-only candidate exchange exporter and verifier for desktop SQLite databases."""

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

FORMAT = "travelflow-desktop-exchange-candidate/0-draft"
STATUS = "candidate"
MANIFEST = "manifest.json"
EXCHANGE = "exchange.json"
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024

REQUIRED_SCHEMA = {
    "sessions": {"id", "title", "created_at", "updated_at", "archived"},
    "branches": {
        "id",
        "session_id",
        "title",
        "parent_branch_id",
        "fork_version_id",
        "is_primary",
        "created_at",
    },
    "versions": {
        "id",
        "branch_id",
        "seq",
        "summary",
        "detail_level",
        "trigger",
        "checkpoint_id",
        "created_at",
    },
    "plans": {"version_id", "markdown", "citations", "todos", "searched_at", "itinerary"},
    "receipt_assets": {
        "id",
        "session_id",
        "digest",
        "original_data",
        "preview_data",
        "original_mime",
        "width",
        "height",
        "created_at",
    },
    "photo_assets": {
        "id",
        "session_id",
        "digest",
        "image_data",
        "width",
        "height",
        "created_at",
    },
}

FIELD_SOURCES = {
    "sessions": (
        "SQLite table sessions(id, title, created_at, updated_at, archived, initial_preferences)"
    ),
    "branches": (
        "SQLite table branches(id, session_id, title, parent_branch_id, "
        "fork_version_id, is_primary, created_at)"
    ),
    "versions": (
        "SQLite table versions(id, branch_id, seq, summary, detail_level, trigger, "
        "checkpoint_id, created_at); ordered by (branch_id, seq)"
    ),
    "version_parent_relation": (
        "Derived conservatively from branches.fork_version_id and intra-branch versions.seq "
        "(desktop versions table has no explicit parent_version_id column)"
    ),
    "plans": (
        "SQLite table plans(version_id, markdown, citations, todos, searched_at, "
        "itinerary, requirements)"
    ),
    "raw_itinerary_json": (
        "Exact UTF-8 text from plans.itinerary written losslessly to itineraries/<version_id>.json "
        "after strict JSON syntax and duplicate-key validation; unknown fields preserved"
    ),
    "expenses": (
        "Extracted directly from parsed plans.itinerary['expenses'] without stripping unknown "
        "fields or coercing missing/null values; amount_cents validated as exact non-negative int"
    ),
    "attachment_refs": (
        "Extracted from plans.itinerary['expenses'][*]['receipt_ids'], "
        "plans.itinerary['recap']['photos'][*]['id'], and "
        "plans.itinerary['recap']['cover_photo_id']"
    ),
    "receipt_preview": (
        "SQLite table receipt_assets(id, session_id, preview_data, digest, width, height, "
        "created_at); route_key='receipts/<id>'"
    ),
    "receipt_original": (
        "SQLite table receipt_assets(id, session_id, original_data, digest, original_mime, "
        "width, height, created_at); route_key='receipts/<id>?original=true'"
    ),
    "travel_photo": (
        "SQLite table photo_assets(id, session_id, image_data, digest, width, height, "
        "created_at); route_key='travel-photos/<id>'"
    ),
}

UNCOVERED_ITEMS = [
    (
        "Candidate read-only export only; not a frozen shared write contract "
        "(shared_contract_version is null)."
    ),
    "LangGraph checkpoints and writes tables are not exported.",
    "chat_receipts, travel_preferences, and workflow_* tables are not exported.",
    (
        "Desktop versions table has no explicit parent_version_id column; "
        "intra-branch links are seq-ordered candidates."
    ),
    (
        "Mobile reader receipt, Android runtime verification, write commands, "
        "conflict merge, and personal database migration are out of scope."
    ),
]


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key is not allowed: {key!r}")
        result[key] = value
    return result


def _reject_non_standard_constant(token):
    raise ValueError(f"Non-standard JSON constant is not allowed: {token!r}")


def parse_strict_json(raw_text, *, label):
    if not isinstance(raw_text, str):
        raise ValueError(f"{label} must be a JSON string.")
    try:
        return json.loads(
            raw_text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_standard_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from error


def _validate_safe_id(value, *, label):
    if (
        not isinstance(value, str)
        or not value
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or value in (".", "..")
        or ".." in value
    ):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_bytes_exact(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"size": len(data), "sha256": _sha256_bytes(data)}


@contextmanager
def _exclusive_output_dir(target):
    target = Path(target)
    if not target.parent.exists():
        raise ValueError(f"Output parent directory does not exist: {target.parent}")
    resolved = target.parent.resolve(strict=True) / target.name
    try:
        resolved.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("Output directory already exists; choose a new directory.") from None
    try:
        yield resolved
    except BaseException:
        shutil.rmtree(resolved, ignore_errors=True)
        raise


@contextmanager
def _open_readonly_source(source):
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"Source file does not exist: {source}")
    resolved = source.resolve(strict=True)
    if resolved.suffix == ".gz":
        with tempfile.TemporaryDirectory(prefix="travelflow-exchange-src-") as tmp:
            unpacked = Path(tmp) / "source.sqlite"
            hasher = hashlib.sha256()
            total = 0
            try:
                with gzip.open(resolved, "rb") as src, unpacked.open("xb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_DECOMPRESSED_BYTES:
                            raise ValueError("Decompressed source exceeds size limit.")
                        hasher.update(chunk)
                        dst.write(chunk)
            except (OSError, EOFError, gzip.BadGzipFile) as error:
                raise ValueError(f"Failed to decompress gzip source: {error}") from error
            yield unpacked, "sqlite_gzip", hasher.hexdigest(), total
    else:
        digest = _sha256_file(resolved)
        size = resolved.stat().st_size
        yield resolved, "sqlite", digest, size


def _validate_sqlite_source(db):
    db.execute("PRAGMA trusted_schema=OFF")
    tables = sorted(
        row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    )
    table_set = set(tables)
    missing_tables = sorted(REQUIRED_SCHEMA.keys() - table_set)
    if missing_tables:
        raise ValueError(f"Source database is missing required tables: {', '.join(missing_tables)}")
    table_columns = {}
    for table, required_cols in REQUIRED_SCHEMA.items():
        cols = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        missing_cols = sorted(required_cols - cols)
        if missing_cols:
            raise ValueError(
                f"Source table {table} is missing required columns: {', '.join(missing_cols)}"
            )
        table_columns[table] = cols
    integrity_rows = [tuple(row) for row in db.execute("PRAGMA integrity_check").fetchall()]
    if integrity_rows != [("ok",)]:
        raise ValueError("SQLite integrity_check failed.")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("SQLite foreign_key_check failed.")
    app_id = db.execute("PRAGMA application_id").fetchone()[0]
    user_version = db.execute("PRAGMA user_version").fetchone()[0]
    return tables, table_columns, app_id, user_version


def _extract_expenses_and_refs(itinerary, *, version_id, receipt_ids_set, photo_ids_set):
    if not isinstance(itinerary, dict):
        raise ValueError(f"Version {version_id} itinerary must be a JSON object.")

    raw_expenses = itinerary.get("expenses")
    if raw_expenses is None:
        expenses = []
    elif isinstance(raw_expenses, list):
        expenses = raw_expenses
    else:
        raise ValueError(f"Version {version_id} itinerary.expenses must be a list.")

    total_cents = 0
    referenced_receipts = []
    seen_receipts = set()
    for index, expense in enumerate(expenses):
        if not isinstance(expense, dict):
            raise ValueError(f"Version {version_id} expense[{index}] must be an object.")
        amount = expense.get("amount_cents")
        if type(amount) is not int or amount < 0:
            raise ValueError(
                f"Version {version_id} expense[{index}].amount_cents must be a non-negative int."
            )
        total_cents += amount
        rids = expense.get("receipt_ids")
        if rids is not None:
            if not isinstance(rids, list):
                raise ValueError(f"Version {version_id} expense[{index}].receipt_ids must be list.")
            for rid in rids:
                _validate_safe_id(rid, label=f"version {version_id} receipt_id")
                if rid not in receipt_ids_set:
                    raise ValueError(
                        f"Version {version_id} references missing receipt asset: {rid}"
                    )
                if rid not in seen_receipts:
                    seen_receipts.add(rid)
                    referenced_receipts.append(rid)

    referenced_photos = []
    seen_photos = set()
    cover_photo_id = None
    recap = itinerary.get("recap")
    if recap is not None:
        if not isinstance(recap, dict):
            raise ValueError(f"Version {version_id} itinerary.recap must be an object.")
        photos = recap.get("photos")
        if photos is not None:
            if not isinstance(photos, list):
                raise ValueError(f"Version {version_id} itinerary.recap.photos must be a list.")
            for p_idx, photo in enumerate(photos):
                if not isinstance(photo, dict):
                    raise ValueError(
                        f"Version {version_id} itinerary.recap.photos[{p_idx}] must be an object."
                    )
                pid = photo.get("id")
                _validate_safe_id(pid, label=f"version {version_id} photo_id")
                if pid not in photo_ids_set:
                    raise ValueError(f"Version {version_id} references missing photo asset: {pid}")
                if pid not in seen_photos:
                    seen_photos.add(pid)
                    referenced_photos.append(pid)
        raw_cover = recap.get("cover_photo_id")
        if raw_cover is not None:
            _validate_safe_id(raw_cover, label=f"version {version_id} cover_photo_id")
            if raw_cover not in photo_ids_set:
                raise ValueError(
                    f"Version {version_id} references missing cover photo asset: {raw_cover}"
                )
            cover_photo_id = raw_cover

    return (
        expenses,
        total_cents,
        {
            "receipt_ids": referenced_receipts,
            "photo_ids": referenced_photos,
            "cover_photo_id": cover_photo_id,
        },
    )


def export_exchange_bundle(source, output):
    """Export a read-only candidate exchange bundle from a desktop SQLite or .sqlite.gz file."""
    with _open_readonly_source(source) as (db_path, input_kind, db_sha256, db_size):
        uri = db_path.as_uri() + "?mode=ro&immutable=1"
        try:
            with closing(sqlite3.connect(uri, uri=True)) as db:
                db.row_factory = sqlite3.Row
                tables, table_columns, app_id, user_version = _validate_sqlite_source(db)
                has_initial_prefs = "initial_preferences" in table_columns["sessions"]
                has_requirements = "requirements" in table_columns["plans"]

                session_sql = (
                    "SELECT id, title, created_at, updated_at, archived, initial_preferences "
                    "FROM sessions ORDER BY id"
                    if has_initial_prefs
                    else (
                        "SELECT id, title, created_at, updated_at, archived, "
                        "NULL AS initial_preferences FROM sessions ORDER BY id"
                    )
                )
                session_rows = db.execute(session_sql).fetchall()
                if not session_rows:
                    raise ValueError("Source database contains no sessions.")

                sessions = []
                session_ids = set()
                for row in session_rows:
                    sid = _validate_safe_id(row["id"], label="session id")
                    session_ids.add(sid)
                    prefs_raw = row["initial_preferences"]
                    if prefs_raw is not None:
                        parse_strict_json(prefs_raw, label=f"session {sid} initial_preferences")
                    sessions.append(
                        {
                            "id": sid,
                            "title": row["title"],
                            "created_at": row["created_at"],
                            "updated_at": row["updated_at"],
                            "archived": bool(row["archived"]),
                            "initial_preferences_raw": prefs_raw,
                        }
                    )

                branch_rows = db.execute(
                    "SELECT id, session_id, title, parent_branch_id, fork_version_id, "
                    "is_primary, created_at FROM branches ORDER BY session_id, is_primary DESC, id"
                ).fetchall()
                branches_by_id = {}
                for row in branch_rows:
                    bid = _validate_safe_id(row["id"], label="branch id")
                    branches_by_id[bid] = dict(row)

                version_rows = db.execute(
                    "SELECT id, branch_id, seq, summary, detail_level, trigger, checkpoint_id, "
                    "created_at FROM versions ORDER BY branch_id, seq, id"
                ).fetchall()
                versions_by_id = {}
                versions_by_branch = {bid: [] for bid in branches_by_id}
                for row in version_rows:
                    vid = _validate_safe_id(row["id"], label="version id")
                    bid = row["branch_id"]
                    if bid not in branches_by_id:
                        raise ValueError(f"Version {vid} references unknown branch: {bid}")
                    seq = row["seq"]
                    if type(seq) is not int or seq < 1:
                        raise ValueError(f"Version {vid} has invalid seq: {seq!r}")
                    item = dict(row)
                    versions_by_id[vid] = item
                    versions_by_branch[bid].append(item)

                for bid, branch in branches_by_id.items():
                    parent_bid = branch["parent_branch_id"]
                    fork_vid = branch["fork_version_id"]
                    if parent_bid is not None:
                        if parent_bid == bid or parent_bid not in branches_by_id:
                            raise ValueError(f"Branch {bid} has invalid parent_branch_id.")
                        if branches_by_id[parent_bid]["session_id"] != branch["session_id"]:
                            raise ValueError(f"Branch {bid} parent belongs to another session.")
                    if fork_vid is not None:
                        if fork_vid not in versions_by_id:
                            raise ValueError(f"Branch {bid} references missing fork_version_id.")
                        fork_branch = versions_by_id[fork_vid]["branch_id"]
                        if parent_bid is not None and fork_branch != parent_bid:
                            raise ValueError(
                                f"Branch {bid} fork_version_id {fork_vid} does not belong to "
                                f"parent_branch_id {parent_bid}."
                            )
                        if branches_by_id[fork_branch]["session_id"] != branch["session_id"]:
                            raise ValueError(f"Branch {bid} fork_version belongs to other session.")

                plan_sql = (
                    "SELECT version_id, markdown, citations, todos, searched_at, itinerary, "
                    "requirements FROM plans"
                    if has_requirements
                    else "SELECT version_id, markdown, citations, todos, searched_at, itinerary, "
                    "NULL AS requirements FROM plans"
                )
                plans_by_version = {
                    row["version_id"]: dict(row) for row in db.execute(plan_sql).fetchall()
                }

                receipt_rows = db.execute(
                    "SELECT id, session_id, digest, original_data, preview_data, original_mime, "
                    "width, height, created_at FROM receipt_assets ORDER BY session_id, id"
                ).fetchall()
                photo_rows = db.execute(
                    "SELECT id, session_id, digest, image_data, width, height, created_at "
                    "FROM photo_assets ORDER BY session_id, id"
                ).fetchall()

                receipt_ids_by_session = {sid: set() for sid in session_ids}
                photo_ids_by_session = {sid: set() for sid in session_ids}
                attachment_blobs = []
                for row in receipt_rows:
                    rid = _validate_safe_id(row["id"], label="receipt id")
                    sid = row["session_id"]
                    receipt_ids_by_session[sid].add(rid)
                    preview_bytes = bytes(row["preview_data"])
                    original_bytes = bytes(row["original_data"])
                    attachment_blobs.append(
                        (
                            f"attachments/receipts/{rid}.preview.bin",
                            preview_bytes,
                            {
                                "route_key": f"receipts/{rid}",
                                "asset_id": rid,
                                "session_id": sid,
                                "kind": "receipt_preview",
                                "relative_path": f"attachments/receipts/{rid}.preview.bin",
                                "source_table": "receipt_assets",
                                "source_column": "preview_data",
                                "source_digest": row["digest"],
                                "source_mime": None,
                                "width": row["width"],
                                "height": row["height"],
                                "created_at": row["created_at"],
                            },
                        )
                    )
                    attachment_blobs.append(
                        (
                            f"attachments/receipts/{rid}.original.bin",
                            original_bytes,
                            {
                                "route_key": f"receipts/{rid}?original=true",
                                "asset_id": rid,
                                "session_id": sid,
                                "kind": "receipt_original",
                                "relative_path": f"attachments/receipts/{rid}.original.bin",
                                "source_table": "receipt_assets",
                                "source_column": "original_data",
                                "source_digest": row["digest"],
                                "source_mime": row["original_mime"],
                                "width": row["width"],
                                "height": row["height"],
                                "created_at": row["created_at"],
                            },
                        )
                    )
                for row in photo_rows:
                    pid = _validate_safe_id(row["id"], label="photo id")
                    sid = row["session_id"]
                    photo_ids_by_session[sid].add(pid)
                    image_bytes = bytes(row["image_data"])
                    attachment_blobs.append(
                        (
                            f"attachments/travel-photos/{pid}.bin",
                            image_bytes,
                            {
                                "route_key": f"travel-photos/{pid}",
                                "asset_id": pid,
                                "session_id": sid,
                                "kind": "travel_photo",
                                "relative_path": f"attachments/travel-photos/{pid}.bin",
                                "source_table": "photo_assets",
                                "source_column": "image_data",
                                "source_digest": row["digest"],
                                "source_mime": None,
                                "width": row["width"],
                                "height": row["height"],
                                "created_at": row["created_at"],
                            },
                        )
                    )

                branches_out = []
                versions_out = []
                itinerary_blobs = []
                for bid, branch in branches_by_id.items():
                    branch_versions = versions_by_branch[bid]
                    seen_seqs = set()
                    for item in branch_versions:
                        if item["seq"] in seen_seqs:
                            raise ValueError(
                                f"Branch {bid} has duplicate version seq: {item['seq']}"
                            )
                        seen_seqs.add(item["seq"])

                    fork_vid = branch["fork_version_id"]
                    fork_source_branch = (
                        versions_by_id[fork_vid]["branch_id"] if fork_vid is not None else None
                    )
                    branches_out.append(
                        {
                            "id": bid,
                            "session_id": branch["session_id"],
                            "title": branch["title"],
                            "parent_branch_id": branch["parent_branch_id"],
                            "fork_version_id": fork_vid,
                            "fork_source_branch_id": fork_source_branch,
                            "is_primary": bool(branch["is_primary"]),
                            "created_at": branch["created_at"],
                            "version_ids": [item["id"] for item in branch_versions],
                        }
                    )

                    prev_vid = None
                    for idx, item in enumerate(branch_versions):
                        vid = item["id"]
                        plan = plans_by_version.get(vid)
                        if plan is None or plan["itinerary"] is None:
                            raise ValueError(f"Version {vid} is missing plans.itinerary.")

                        raw_itinerary = plan["itinerary"]
                        parsed_itinerary = parse_strict_json(
                            raw_itinerary, label=f"version {vid} plans.itinerary"
                        )
                        parse_strict_json(plan["citations"], label=f"version {vid} plans.citations")
                        parse_strict_json(plan["todos"], label=f"version {vid} plans.todos")
                        if plan["requirements"] is not None:
                            parse_strict_json(
                                plan["requirements"], label=f"version {vid} plans.requirements"
                            )

                        expenses, total_cents, attachment_refs = _extract_expenses_and_refs(
                            parsed_itinerary,
                            version_id=vid,
                            receipt_ids_set=receipt_ids_by_session[branch["session_id"]],
                            photo_ids_set=photo_ids_by_session[branch["session_id"]],
                        )

                        if idx == 0:
                            if fork_vid is not None:
                                parent_relation = {
                                    "kind": "fork_version_id",
                                    "parent_version_id": fork_vid,
                                    "evidence": f"branches[{bid}].fork_version_id",
                                }
                            else:
                                parent_relation = {
                                    "kind": "branch_root",
                                    "parent_version_id": None,
                                    "evidence": f"branches[{bid}].fork_version_id is null",
                                }
                        else:
                            parent_relation = {
                                "kind": "branch_seq_predecessor",
                                "parent_version_id": prev_vid,
                                "evidence": f"versions.seq order within branch {bid}",
                            }
                        prev_vid = vid

                        rel_itinerary = f"itineraries/{vid}.json"
                        raw_bytes = raw_itinerary.encode("utf-8")
                        itinerary_blobs.append((rel_itinerary, raw_bytes))
                        versions_out.append(
                            {
                                "id": vid,
                                "session_id": branch["session_id"],
                                "branch_id": bid,
                                "seq": item["seq"],
                                "summary": item["summary"],
                                "detail_level": item["detail_level"],
                                "trigger": item["trigger"],
                                "checkpoint_id": item["checkpoint_id"],
                                "created_at": item["created_at"],
                                "parent_relation": parent_relation,
                                "plan": {
                                    "markdown": plan["markdown"],
                                    "searched_at": plan["searched_at"],
                                    "citations_raw": plan["citations"],
                                    "todos_raw": plan["todos"],
                                    "requirements_raw": plan["requirements"],
                                    "raw_itinerary_path": rel_itinerary,
                                    "raw_itinerary_sha256": _sha256_bytes(raw_bytes),
                                    "raw_itinerary_size": len(raw_bytes),
                                },
                                "expenses": expenses,
                                "expenses_total_cents": total_cents,
                                "attachment_refs": attachment_refs,
                            }
                        )
        except sqlite3.Error as error:
            raise ValueError(f"Failed to read SQLite source ({error}).") from error

        source_meta = {
            "input_kind": input_kind,
            "database_sha256": db_sha256,
            "database_size": db_size,
            "sqlite_application_id": app_id,
            "sqlite_user_version": user_version,
            "tables": tables,
        }

        with _exclusive_output_dir(output) as folder:
            files_index = {}
            for rel_path, raw_bytes in itinerary_blobs:
                files_index[rel_path] = _write_bytes_exact(folder / rel_path, raw_bytes)

            attachments_out = []
            for rel_path, blob, meta in attachment_blobs:
                written = _write_bytes_exact(folder / rel_path, blob)
                files_index[rel_path] = written
                attachments_out.append({**meta, **written})

            exchange_doc = {
                "format": FORMAT,
                "status": STATUS,
                "shared_contract_version": None,
                "source": source_meta,
                "field_sources": FIELD_SOURCES,
                "uncovered_items": UNCOVERED_ITEMS,
                "sessions": sessions,
                "branches": branches_out,
                "versions": versions_out,
                "attachments": attachments_out,
            }
            exchange_bytes = (
                json.dumps(exchange_doc, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            files_index[EXCHANGE] = _write_bytes_exact(folder / EXCHANGE, exchange_bytes)

            manifest_doc = {
                "format": FORMAT,
                "status": STATUS,
                "shared_contract_version": None,
                "read_only_export": True,
                "source": source_meta,
                "exchange_file": EXCHANGE,
                "files": dict(sorted(files_index.items())),
                "summary": {
                    "sessions": len(sessions),
                    "branches": len(branches_out),
                    "versions": len(versions_out),
                    "attachments": len(attachments_out),
                },
            }
            manifest_bytes = (
                json.dumps(manifest_doc, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            staged_manifest = folder / ".manifest"
            _write_bytes_exact(staged_manifest, manifest_bytes)
            staged_manifest.rename(folder / MANIFEST)

            return {
                "bundle_dir": str(folder),
                "format": FORMAT,
                "status": STATUS,
                "shared_contract_version": None,
                "manifest_sha256": _sha256_bytes(manifest_bytes),
                "exchange_sha256": files_index[EXCHANGE]["sha256"],
                "database_sha256": db_sha256,
                "sessions": len(sessions),
                "branches": len(branches_out),
                "versions": len(versions_out),
                "attachments": len(attachments_out),
            }


def _safe_bundle_file(bundle_root, rel_path):
    if not isinstance(rel_path, str) or not rel_path or Path(rel_path).is_absolute():
        raise ValueError(f"Invalid relative bundle path: {rel_path!r}")
    candidate = bundle_root / rel_path
    if candidate.is_symlink():
        raise ValueError(f"Symlinks are not allowed in bundle: {rel_path}")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(bundle_root) or not resolved.is_file():
        raise ValueError(f"Bundle file escapes root or is missing: {rel_path}")
    return resolved


def verify_exchange_bundle(bundle_dir, *, expected_path=None):
    """Independently verify a candidate exchange bundle and optional expected.json."""
    bundle_root = Path(bundle_dir)
    if not bundle_root.is_dir():
        raise ValueError(f"Bundle directory does not exist: {bundle_dir}")
    bundle_root = bundle_root.resolve(strict=True)

    manifest_path = bundle_root / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Bundle is incomplete: missing manifest.json.")
    manifest = parse_strict_json(
        manifest_path.read_text(encoding="utf-8"), label="manifest.json"
    )
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must be a JSON object.")
    if manifest.get("format") != FORMAT or manifest.get("status") != STATUS:
        raise ValueError("Unsupported or invalid candidate exchange format.")
    if manifest.get("shared_contract_version") is not None:
        raise ValueError("Candidate exchange bundle must not claim a frozen contract version.")

    files = manifest.get("files")
    if not isinstance(files, dict) or EXCHANGE not in files:
        raise ValueError("manifest.json is missing required file index.")

    for rel_path, spec in files.items():
        if not isinstance(spec, dict):
            raise ValueError(f"Invalid file entry in manifest: {rel_path}")
        try:
            file_path = _safe_bundle_file(bundle_root, rel_path)
        except FileNotFoundError as error:
            raise ValueError(f"Missing bundle file: {rel_path}") from error
        expected_size = spec.get("size")
        expected_sha = spec.get("sha256")
        if type(expected_size) is not int or file_path.stat().st_size != expected_size:
            raise ValueError(f"File size mismatch for {rel_path}.")
        actual_sha = _sha256_file(file_path)
        if actual_sha != expected_sha:
            raise ValueError(f"SHA-256 mismatch for {rel_path}.")

    exchange = parse_strict_json(
        (bundle_root / EXCHANGE).read_text(encoding="utf-8"), label="exchange.json"
    )
    if not isinstance(exchange, dict):
        raise ValueError("exchange.json must be a JSON object.")
    if exchange.get("format") != FORMAT or exchange.get("status") != STATUS:
        raise ValueError("exchange.json format marker mismatch.")

    attachments_by_route = {}
    receipt_ids = set()
    photo_ids = set()
    for att in exchange.get("attachments", []):
        rel_path = att["relative_path"]
        if rel_path not in files:
            raise ValueError(f"Attachment missing from manifest files: {rel_path}")
        if files[rel_path]["sha256"] != att["sha256"] or files[rel_path]["size"] != att["size"]:
            raise ValueError(
                f"Attachment digest mismatch between exchange and manifest: {rel_path}"
            )
        attachments_by_route[att["route_key"]] = att
        if att["kind"] in ("receipt_preview", "receipt_original"):
            receipt_ids.add(att["asset_id"])
        elif att["kind"] == "travel_photo":
            photo_ids.add(att["asset_id"])

    versions_by_id = {}
    for version in exchange.get("versions", []):
        vid = version["id"]
        versions_by_id[vid] = version
        plan = version["plan"]
        rel_itinerary = plan["raw_itinerary_path"]
        if rel_itinerary not in files:
            raise ValueError(f"Raw itinerary missing from manifest files: {rel_itinerary}")
        raw_text = (bundle_root / rel_itinerary).read_text(encoding="utf-8")
        raw_bytes = raw_text.encode("utf-8")
        if _sha256_bytes(raw_bytes) != plan["raw_itinerary_sha256"]:
            raise ValueError(f"Raw itinerary SHA-256 mismatch for version {vid}.")
        parsed_itinerary = parse_strict_json(raw_text, label=rel_itinerary)
        expenses, total_cents, refs = _extract_expenses_and_refs(
            parsed_itinerary,
            version_id=vid,
            receipt_ids_set=receipt_ids,
            photo_ids_set=photo_ids,
        )
        if version["expenses"] != expenses:
            raise ValueError(f"Expenses mismatch with raw itinerary JSON for version {vid}.")
        if version["expenses_total_cents"] != total_cents:
            raise ValueError(f"Expenses total mismatch for version {vid}.")
        if version["attachment_refs"] != refs:
            raise ValueError(f"Attachment refs mismatch for version {vid}.")

    if expected_path is not None:
        expected = parse_strict_json(
            Path(expected_path).read_text(encoding="utf-8"), label="expected.json"
        )
        if exchange["source"]["database_sha256"] != expected["database_sha256"]:
            raise ValueError("Source database_sha256 does not match expected.json.")
        sessions = {s["id"]: s for s in exchange["sessions"]}
        if expected["session"] not in sessions:
            raise ValueError("Expected session not found in bundle.")
        if sessions[expected["session"]]["title"] != expected["title"]:
            raise ValueError("Session title does not match expected.json.")

        branches = {b["id"]: b for b in exchange["branches"]}
        main_b = branches.get(expected["main_branch"])
        fork_b = branches.get(expected["fork_branch"])
        if not main_b or not fork_b:
            raise ValueError("Expected main or fork branch not found in bundle.")
        if main_b["version_ids"] != expected["main_versions"]:
            raise ValueError("Main branch version order does not match expected.json.")
        if fork_b["version_ids"] != expected["fork_versions"]:
            raise ValueError("Fork branch version order does not match expected.json.")
        if fork_b["fork_version_id"] != expected["fork_from"]:
            raise ValueError("Fork common ancestor does not match expected.json.")
        if fork_b["parent_branch_id"] != expected["main_branch"]:
            raise ValueError("Fork parent_branch_id does not match expected main branch.")

        first_ver, second_ver, third_ver = expected["main_versions"]
        fork_ver = expected["fork_versions"][0]
        if versions_by_id[first_ver]["expenses_total_cents"] != 0:
            raise ValueError("Main branch v1 expenses_total_cents should be 0.")
        if versions_by_id[second_ver]["expenses_total_cents"] != expected["amount_cents"]:
            raise ValueError("Main branch v2 expenses_total_cents mismatch.")
        if versions_by_id[third_ver]["expenses_total_cents"] != expected["amount_cents"]:
            raise ValueError("Main branch v3 expenses_total_cents mismatch.")
        if versions_by_id[fork_ver]["expenses_total_cents"] != 0:
            raise ValueError("Fork branch v1 expenses_total_cents should be 0.")

        for route_key, expected_sha in expected["attachments_sha256"].items():
            att = attachments_by_route.get(route_key)
            if not att:
                raise ValueError(f"Expected attachment route missing: {route_key}")
            if att["sha256"] != expected_sha:
                raise ValueError(f"Attachment SHA-256 mismatch for {route_key}.")

    return {
        "verified": True,
        "bundle_dir": str(bundle_root),
        "format": FORMAT,
        "database_sha256": exchange["source"]["database_sha256"],
        "sessions": len(exchange["sessions"]),
        "branches": len(exchange["branches"]),
        "versions": len(exchange["versions"]),
        "attachments": len(exchange["attachments"]),
        "attachments_sha256": {
            route: att["sha256"] for route, att in sorted(attachments_by_route.items())
        },
        "expected_checked": expected_path is not None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="Export a read-only candidate exchange bundle."
    )
    export_parser.add_argument(
        "--source", type=Path, required=True, help="Explicit path to .sqlite or .sqlite.gz"
    )
    export_parser.add_argument(
        "--output", type=Path, required=True, help="Non-existent output directory"
    )

    verify_parser = subparsers.add_parser(
        "verify", help="Verify an exported candidate exchange bundle."
    )
    verify_parser.add_argument(
        "--bundle", type=Path, required=True, help="Path to exported bundle directory"
    )
    verify_parser.add_argument(
        "--expected", type=Path, help="Optional expected.json for independent assertions"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_exchange_bundle(args.source, args.output)
        else:
            result = verify_exchange_bundle(args.bundle, expected_path=args.expected)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
