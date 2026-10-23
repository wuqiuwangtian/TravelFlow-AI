"""SQLite 连接与表结构。

表设计对应规划 §2.4。四张业务表与 LangGraph 自带的 checkpoint 表
**共存于同一个数据库文件**：前者是给界面用的轻量索引，后者存完整状态。

选择手写 SQL 而非 ORM：表结构简单、查询固定，引入 ORM 只会增加抽象层。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA = """
-- 本机常用偏好。保留修订号，防止旧页面在删除后重新写回旧内容。
CREATE TABLE IF NOT EXISTS travel_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    preferences TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
INSERT OR IGNORE INTO travel_preferences(id) VALUES (1);

-- 会话
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);

-- 分支（一条演化线；id 同时作为 LangGraph thread_id）
CREATE TABLE IF NOT EXISTS branches (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    title               TEXT NOT NULL DEFAULT '主方案',
    parent_branch_id    TEXT REFERENCES branches(id) ON DELETE SET NULL,
    fork_version_id     TEXT,
    is_primary          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);

-- 版本（分支上的快照，对应一个 LangGraph checkpoint）
CREATE TABLE IF NOT EXISTS versions (
    id              TEXT PRIMARY KEY,
    branch_id       TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    detail_level    TEXT NOT NULL DEFAULT 'L2_standard',
    trigger         TEXT NOT NULL DEFAULT 'user_message',
    checkpoint_id   TEXT,
    created_at      TEXT NOT NULL
);

-- 路书产出
CREATE TABLE IF NOT EXISTS plans (
    version_id   TEXT PRIMARY KEY REFERENCES versions(id) ON DELETE CASCADE,
    markdown     TEXT NOT NULL,
    citations    TEXT NOT NULL DEFAULT '[]',
    todos        TEXT NOT NULL DEFAULT '[]',
    searched_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_branches_session ON branches(session_id);
CREATE INDEX IF NOT EXISTS idx_versions_branch  ON versions(branch_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);

-- 凭证保留原图，预览与识别使用清除元数据后的副本；删除会话时清理。
CREATE TABLE IF NOT EXISTS receipt_assets (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    digest TEXT NOT NULL,
    original_data BLOB NOT NULL,
    preview_data BLOB NOT NULL,
    original_mime TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, digest)
);
CREATE INDEX IF NOT EXISTS idx_receipts_session ON receipt_assets(session_id);

-- 旅行照片独立于消费凭证，只保存已清除元数据的展示图。
CREATE TABLE IF NOT EXISTS photo_assets (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    digest TEXT NOT NULL,
    image_data BLOB NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, digest)
);
CREATE INDEX IF NOT EXISTS idx_photos_session ON photo_assets(session_id);

-- 已完成的对话请求回执；重试同一次发送时复用结果，随分支和旅行删除。
CREATE TABLE IF NOT EXISTS chat_receipts (
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    outcome TEXT NOT NULL,
    PRIMARY KEY(branch_id, request_id)
);
"""


async def create_connection(db_path: str | Path) -> aiosqlite.Connection:
    """打开连接、开启 WAL 并确保表结构存在。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row

    # WAL 模式让读不阻塞写，适合「边生成边查看历史」的场景
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    # 轻量迁移：老库补齐后加的列（幂等）
    await _ensure_column(conn, "plans", "todos", "TEXT NOT NULL DEFAULT '[]'")
    await _ensure_column(conn, "plans", "itinerary", "TEXT")
    await _ensure_column(conn, "plans", "requirements", "TEXT")
    await _ensure_column(conn, "sessions", "initial_preferences", "TEXT NOT NULL DEFAULT '[]'")
    await conn.commit()

    logger.info("数据库就绪", extra={"extra_fields": {"path": str(path)}})
    return conn


async def _ensure_column(
    conn: aiosqlite.Connection, table: str, column: str, definition: str
) -> None:
    """列不存在时补上 —— 让升级安装不必删库重建。"""
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    existing = {row["name"] for row in await cursor.fetchall()}
    if column not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@asynccontextmanager
async def open_database(db_path: str | Path) -> AsyncIterator[aiosqlite.Connection]:
    """上下文管理器形式的连接（测试与脚本用）。"""
    conn = await create_connection(db_path)
    try:
        yield conn
    finally:
        await conn.close()
