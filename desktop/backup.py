"""Desktop whole-database backups. Only complete, verified copies are published."""

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

SNAPSHOT = "snapshot.sqlite"
MANIFEST = "manifest.json"
FORMAT = "travelflow-desktop-backup/1"
CORE = {
    "sessions": {"id", "title"},
    "branches": {"id", "session_id"},
    "versions": {"id", "branch_id", "seq"},
    "plans": {"version_id", "markdown"},
}


def absolute_path(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("请填写当前运行环境中的绝对路径。")
    return Path(value)


@contextmanager
def fresh_directory(target):
    # Never resolve away a pre-existing final symlink (including broken ones).
    target = target.parent.resolve(strict=True) / target.name
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("目标已存在，请为本次操作选择一个新的目录。") from None
    try:
        yield target
    except BaseException:
        # Only remove files this operation could create in its exclusive directory.
        for name in (
            ".partial",
            ".partial-journal",
            ".partial-wal",
            ".partial-shm",
            SNAPSHOT,
            ".manifest",
            MANIFEST,
            "travel.sqlite",
        ):
            (target / name).unlink(missing_ok=True)
        target.rmdir()
        raise


def validate_database(path):
    uri = path.as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if not CORE.keys() <= tables:
            raise ValueError("文件缺少 TravelFlow 行程数据表。")
        for table, required in CORE.items():
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not required <= columns:
                raise ValueError("文件缺少 TravelFlow 行程数据表或必要字段。")
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite 完整性校验失败。")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite 数据引用校验失败。")


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def create_backup(source, target):
    if not source.is_file():
        raise ValueError("源数据库不存在，请重新核对数据库地址。")
    source = source.resolve(strict=True)
    with fresh_directory(target) as folder:
        staged = folder / ".partial"
        try:
            with (
                closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db,
                closing(sqlite3.connect(staged)) as out,
            ):
                last_progress = time.monotonic()

                def progress(status, remaining, total):
                    nonlocal last_progress
                    if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                        if time.monotonic() - last_progress > 5:
                            raise ValueError("数据库持续繁忙，请结束写入后重试。")
                    else:
                        last_progress = time.monotonic()

                # Pin a committed WAL read snapshot even under continuous writes.
                db.execute("BEGIN")
                db.execute("SELECT count(*) FROM sqlite_schema").fetchone()
                db.backup(out, pages=256, progress=progress)
                out.execute("PRAGMA journal_mode=DELETE").fetchone()
            validate_database(staged)
        except sqlite3.Error as error:
            raise ValueError(f"无法完成 SQLite 备份校验（{error}）。") from error
        info = {"format": FORMAT, "size": staged.stat().st_size, "sha256": checksum(staged)}
        staged.rename(folder / SNAPSHOT)
        marker = folder / ".manifest"
        marker.write_text(json.dumps(info) + "\n", encoding="utf-8")
        marker.rename(folder / MANIFEST)
        return {"kind": "backup", "source": str(source), "path": str(folder), **info}


def restore_backup(package, target):
    package = package.resolve(strict=True)
    if target.resolve().is_relative_to(package):
        raise ValueError("请选择备份包之外的新目录保存副本。")
    manifest = package / MANIFEST
    snapshot = package / SNAPSHOT
    if (
        manifest.is_symlink()
        or snapshot.is_symlink()
        or not manifest.is_file()
        or not snapshot.is_file()
    ):
        raise ValueError("备份包不完整，缺少独立的清单或快照文件。")
    with manifest.open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("备份清单过大。")
    info = json.loads(raw)
    if not isinstance(info, dict) or info.get("format") != FORMAT:
        raise ValueError("无法识别此版本的备份包。")
    if type(info.get("size")) is not int or snapshot.stat().st_size != info["size"]:
        raise ValueError("快照文件大小与备份清单不符。")
    with fresh_directory(target) as folder:
        staged = folder / ".partial"
        with snapshot.open("rb") as src, staged.open("xb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        digest = checksum(staged)
        if digest != info.get("sha256"):
            raise ValueError("快照 SHA-256 与清单不符。")
        try:
            validate_database(staged)
        except sqlite3.Error as error:
            raise ValueError(f"恢复副本的 SQLite 校验失败（{error}）。") from error
        final = folder / "travel.sqlite"
        staged.rename(final)
        return {"kind": "restore", "source": str(package), "path": str(final), "sha256": digest}
