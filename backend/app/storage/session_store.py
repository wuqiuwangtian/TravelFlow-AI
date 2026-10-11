"""会话 / 分支 / 版本的持久化（规划 §2.4）。

设计要点：
- **主键用 uuid，带默认值**，便于测试注入确定值；
- 时间统一以 ISO 字符串存储，读回时还原为 ``datetime``；
- 所有操作都是 ``await``，不会像旧项目那样阻塞事件循环。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import aiosqlite

from app.domain.models import (
    BranchInfo,
    Citation,
    SessionInfo,
    TodoItem,
    VersionDetail,
    VersionInfo,
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime:
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - 防御历史脏数据
        return datetime.now()


class SessionStore:
    """四张业务表的读写入口。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ==================================================== 会话
    async def create_session(self, title: str, *, session_id: str | None = None) -> SessionInfo:
        sid = session_id or uuid.uuid4().hex[:12]
        now = _now()
        await self._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at, archived) VALUES (?,?,?,?,0)",
            (sid, title, now, now),
        )
        await self._conn.commit()
        return SessionInfo(id=sid, title=title, created_at=_parse(now), updated_at=_parse(now))

    async def list_sessions(
        self,
        *,
        archived: bool = False,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionInfo]:
        sql = [
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM branches b WHERE b.session_id = s.id) AS branch_count "
            "FROM sessions s WHERE s.archived = ?"
        ]
        params: list[object] = [1 if archived else 0]
        if query:
            sql.append("AND s.title LIKE ?")
            params.append(f"%{query}%")
        sql.append("ORDER BY s.updated_at DESC LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        cursor = await self._conn.execute(" ".join(sql), params)
        rows = await cursor.fetchall()
        return [self._to_session(row) for row in rows]

    async def get_session(self, session_id: str) -> SessionInfo | None:
        cursor = await self._conn.execute(
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM branches b WHERE b.session_id = s.id) AS branch_count "
            "FROM sessions s WHERE s.id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return self._to_session(row) if row else None

    async def rename_session(self, session_id: str, title: str) -> bool:
        cursor = await self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def set_archived(self, session_id: str, archived: bool) -> bool:
        cursor = await self._conn.execute(
            "UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
            (1 if archived else 0, _now(), session_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_session(self, session_id: str) -> bool:
        cursor = await self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def touch_session(self, session_id: str) -> None:
        await self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
        )
        await self._conn.commit()

    # ==================================================== 分支
    async def create_branch(
        self,
        session_id: str,
        title: str = "主方案",
        *,
        branch_id: str | None = None,
        parent_branch_id: str | None = None,
        fork_version_id: str | None = None,
        is_primary: bool = False,
    ) -> BranchInfo:
        bid = branch_id or uuid.uuid4().hex[:12]
        now = _now()
        await self._conn.execute(
            """INSERT INTO branches(id, session_id, title, parent_branch_id,
                                    fork_version_id, is_primary, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                bid,
                session_id,
                title,
                parent_branch_id,
                fork_version_id,
                1 if is_primary else 0,
                now,
            ),
        )
        await self._conn.commit()
        return BranchInfo(
            id=bid,
            session_id=session_id,
            title=title,
            created_at=_parse(now),
            parent_branch_id=parent_branch_id,
            fork_version_id=fork_version_id,
            is_primary=is_primary,
        )

    async def list_branches(self, session_id: str) -> list[BranchInfo]:
        cursor = await self._conn.execute(
            "SELECT * FROM branches WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
        )
        rows = await cursor.fetchall()
        return [self._to_branch(row) for row in rows]

    async def get_branch(self, branch_id: str) -> BranchInfo | None:
        cursor = await self._conn.execute("SELECT * FROM branches WHERE id = ?", (branch_id,))
        row = await cursor.fetchone()
        return self._to_branch(row) if row else None

    async def set_primary_branch(self, session_id: str, branch_id: str) -> bool:
        await self._conn.execute(
            "UPDATE branches SET is_primary = 0 WHERE session_id = ?", (session_id,)
        )
        cursor = await self._conn.execute(
            "UPDATE branches SET is_primary = 1 WHERE id = ? AND session_id = ?",
            (branch_id, session_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_branch(self, branch_id: str) -> bool:
        branch = await self.get_branch(branch_id)
        if branch is None or branch.is_primary:
            return False  # 主分支不允许删除
        cursor = await self._conn.execute("DELETE FROM branches WHERE id = ?", (branch_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    # ==================================================== 版本
    async def create_version(
        self,
        branch_id: str,
        *,
        summary: str = "",
        detail_level: str = "L2_standard",
        trigger: str = "user_message",
        checkpoint_id: str | None = None,
        version_id: str | None = None,
    ) -> VersionInfo:
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM versions WHERE branch_id = ?", (branch_id,)
        )
        seq = int((await cursor.fetchone())[0])

        vid = version_id or uuid.uuid4().hex[:12]
        now = _now()
        await self._conn.execute(
            """INSERT INTO versions(id, branch_id, seq, summary, detail_level,
                                    trigger, checkpoint_id, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (vid, branch_id, seq, summary, detail_level, trigger, checkpoint_id, now),
        )
        await self._conn.commit()
        return VersionInfo(
            id=vid,
            branch_id=branch_id,
            seq=seq,
            summary=summary,
            detail_level=detail_level,
            trigger=trigger,
            checkpoint_id=checkpoint_id,
            created_at=_parse(now),
        )

    async def list_versions(self, branch_id: str) -> list[VersionInfo]:
        cursor = await self._conn.execute(
            "SELECT * FROM versions WHERE branch_id = ? ORDER BY seq ASC", (branch_id,)
        )
        rows = await cursor.fetchall()
        return [self._to_version(row) for row in rows]

    async def get_version(self, version_id: str) -> VersionInfo | None:
        cursor = await self._conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,))
        row = await cursor.fetchone()
        return self._to_version(row) if row else None

    # ==================================================== 路书
    async def save_plan(
        self,
        version_id: str,
        *,
        markdown: str,
        citations: list[Citation] | None = None,
        todos: list[TodoItem] | None = None,
        searched_at: datetime | None = None,
    ) -> None:
        citations_payload = json.dumps(
            [c.model_dump(mode="json") for c in (citations or [])], ensure_ascii=False
        )
        todos_payload = json.dumps(
            [t.model_dump(mode="json") for t in (todos or [])], ensure_ascii=False
        )
        await self._conn.execute(
            """INSERT INTO plans(version_id, markdown, citations, todos, searched_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(version_id) DO UPDATE SET
                   markdown = excluded.markdown,
                   citations = excluded.citations,
                   todos = excluded.todos,
                   searched_at = excluded.searched_at""",
            (
                version_id,
                markdown,
                citations_payload,
                todos_payload,
                searched_at.isoformat(timespec="seconds") if searched_at else None,
            ),
        )
        await self._conn.commit()

    async def get_version_detail(self, version_id: str) -> VersionDetail | None:
        version = await self.get_version(version_id)
        if version is None:
            return None

        cursor = await self._conn.execute(
            "SELECT markdown, citations, todos, searched_at FROM plans WHERE version_id = ?",
            (version_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return VersionDetail(version=version)

        citations = [Citation(**item) for item in json.loads(row["citations"] or "[]")]
        todos = [TodoItem(**item) for item in json.loads(row["todos"] or "[]")]
        return VersionDetail(
            version=version,
            markdown=row["markdown"] or "",
            citations=citations,
            todos=todos,
            searched_at=datetime.fromisoformat(row["searched_at"]) if row["searched_at"] else None,
        )

    # ==================================================== 映射
    @staticmethod
    def _to_session(row: aiosqlite.Row) -> SessionInfo:
        keys = row.keys()
        return SessionInfo(
            id=row["id"],
            title=row["title"],
            created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
            archived=bool(row["archived"]),
            branch_count=int(row["branch_count"]) if "branch_count" in keys else 0,
        )

    @staticmethod
    def _to_branch(row: aiosqlite.Row) -> BranchInfo:
        return BranchInfo(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            created_at=_parse(row["created_at"]),
            parent_branch_id=row["parent_branch_id"],
            fork_version_id=row["fork_version_id"],
            is_primary=bool(row["is_primary"]),
        )

    @staticmethod
    def _to_version(row: aiosqlite.Row) -> VersionInfo:
        return VersionInfo(
            id=row["id"],
            branch_id=row["branch_id"],
            seq=int(row["seq"]),
            summary=row["summary"] or "",
            detail_level=row["detail_level"],
            trigger=row["trigger"],
            checkpoint_id=row["checkpoint_id"],
            created_at=_parse(row["created_at"]),
        )
