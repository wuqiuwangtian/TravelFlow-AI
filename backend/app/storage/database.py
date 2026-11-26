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

-- 局部节点重跑只保存预览；应用时才生成正式路书版本。
CREATE TABLE IF NOT EXISTS workflow_previews (
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    preview TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(branch_id, request_id)
);

CREATE TABLE IF NOT EXISTS workflow_projects (
    branch_id TEXT PRIMARY KEY REFERENCES branches(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    graph TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(branch_id,id)
);
CREATE TABLE IF NOT EXISTS workflow_adoptions (
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    preview TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(branch_id,request_id)
);

-- 跨旅行复用的个人工程模板；删除后仅留修订墓碑，避免旧请求复活内容。
CREATE TABLE IF NOT EXISTS workflow_user_templates (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    graph TEXT NOT NULL,
    trip_input_ids TEXT NOT NULL,
    revision INTEGER NOT NULL,
    name_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_request_id TEXT NOT NULL,
    last_fingerprint TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_templates_name
    ON workflow_user_templates(name_key) WHERE deleted=0;

-- 跨端契约 0.1.0 接收回执：与 versions/plans 同事务提交，记录 origin_direction 避免回送来源设备。
CREATE TABLE IF NOT EXISTS exchange_operation_receipts (
    dataset_id TEXT NOT NULL,
    source_device_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    canonical_profile TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    canonical_intent_json TEXT NOT NULL,
    effect TEXT NOT NULL,
    result_version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    origin_direction TEXT NOT NULL DEFAULT 'received',
    committed_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, source_device_id, operation_id)
);

-- 跨端反向更正待发队列：桌面更正已确认的手机消费金额时，与 versions/plans 同事务原子落库。
CREATE TABLE IF NOT EXISTS exchange_outbound_operations (
    dataset_id TEXT NOT NULL,
    source_device_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    canonical_profile TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    canonical_intent_json TEXT NOT NULL,
    raw_command_json TEXT NOT NULL,
    expected_revision_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    origin_dataset_id TEXT,
    origin_source_device_id TEXT,
    origin_operation_id TEXT,
    delivery_state TEXT NOT NULL DEFAULT 'unsent' CHECK (
        delivery_state IN ('unsent', 'verification_pending', 'confirmed')
    ),
    confirmed_mobile_revision_id TEXT,
    confirmed_effect TEXT,
    last_error TEXT,
    last_raw_response_json TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (dataset_id, source_device_id, operation_id)
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
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    # 轻量迁移：老库补齐后加的列（幂等）
    await _ensure_column(conn, "plans", "todos", "TEXT NOT NULL DEFAULT '[]'")
    await _ensure_column(conn, "plans", "itinerary", "TEXT")
    await _ensure_column(conn, "plans", "requirements", "TEXT")
    await _ensure_column(conn, "sessions", "initial_preferences", "TEXT NOT NULL DEFAULT '[]'")
    await _ensure_column(
        conn, "exchange_outbound_operations", "confirmed_mobile_revision_id", "TEXT"
    )
    await _ensure_column(conn, "exchange_outbound_operations", "confirmed_effect", "TEXT")
    await _ensure_column(conn, "exchange_outbound_operations", "last_error", "TEXT")
    await _ensure_column(conn, "exchange_outbound_operations", "last_raw_response_json", "TEXT")
    await _ensure_column(conn, "exchange_outbound_operations", "confirmed_at", "TEXT")
    await _ensure_column(conn, "exchange_outbound_operations", "origin_dataset_id", "TEXT")
    await _ensure_column(conn, "exchange_outbound_operations", "origin_source_device_id", "TEXT")
    await _ensure_column(conn, "exchange_outbound_operations", "origin_operation_id", "TEXT")
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
