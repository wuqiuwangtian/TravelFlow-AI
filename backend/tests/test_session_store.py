"""会话 / 分支 / 版本存储层测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest

from app.domain.models import Citation
from app.storage.database import open_database
from app.storage.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SessionStore]:
    async with open_database(tmp_path / "test.db") as conn:
        yield SessionStore(conn)


# ============================================================ 会话
async def test_create_and_get_session(store: SessionStore) -> None:
    created = await store.create_session("重庆之行")
    fetched = await store.get_session(created.id)

    assert fetched is not None
    assert fetched.title == "重庆之行"
    assert fetched.archived is False


async def test_list_sessions_excludes_archived(store: SessionStore) -> None:
    await store.create_session("活跃会话")
    archived = await store.create_session("已归档会话")
    await store.set_archived(archived.id, True)

    active = await store.list_sessions()
    assert [s.title for s in active] == ["活跃会话"]

    archived_list = await store.list_sessions(archived=True)
    assert [s.title for s in archived_list] == ["已归档会话"]


async def test_search_sessions_by_title(store: SessionStore) -> None:
    await store.create_session("重庆之行")
    await store.create_session("北京周末")

    found = await store.list_sessions(query="重庆")

    assert len(found) == 1
    assert found[0].title == "重庆之行"


async def test_branch_count_is_reported(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    await store.create_branch(session.id, "主方案", is_primary=True)
    await store.create_branch(session.id, "亲子版")

    refreshed = await store.get_session(session.id)

    assert refreshed is not None
    assert refreshed.branch_count == 2


async def test_delete_session_cascades(store: SessionStore) -> None:
    session = await store.create_session("待删除")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    await store.create_version(branch.id, summary="v1")

    assert await store.delete_session(session.id) is True
    assert await store.get_session(session.id) is None
    assert await store.list_branches(session.id) == []
    assert await store.list_versions(branch.id) == []


# ============================================================ 分支
async def test_branch_records_lineage(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    primary = await store.create_branch(session.id, "主方案", is_primary=True)
    version = await store.create_version(primary.id, summary="标准版")

    forked = await store.create_branch(
        session.id,
        "亲子版",
        parent_branch_id=primary.id,
        fork_version_id=version.id,
    )

    assert forked.parent_branch_id == primary.id
    assert forked.fork_version_id == version.id
    assert forked.is_primary is False


async def test_primary_branch_cannot_be_deleted(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    primary = await store.create_branch(session.id, "主方案", is_primary=True)

    assert await store.delete_branch(primary.id) is False
    assert len(await store.list_branches(session.id)) == 1


async def test_set_primary_branch_switches_exclusively(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    first = await store.create_branch(session.id, "主方案", is_primary=True)
    second = await store.create_branch(session.id, "亲子版")

    await store.set_primary_branch(session.id, second.id)
    branches = {b.id: b for b in await store.list_branches(session.id)}

    assert branches[second.id].is_primary is True
    assert branches[first.id].is_primary is False


# ============================================================ 版本
async def test_version_sequence_increments_per_branch(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)

    v1 = await store.create_version(branch.id, summary="v1")
    v2 = await store.create_version(branch.id, summary="v2")

    assert (v1.seq, v2.seq) == (1, 2)


async def test_versions_are_isolated_between_branches(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    branch_a = await store.create_branch(session.id, "A", is_primary=True)
    branch_b = await store.create_branch(session.id, "B")

    await store.create_version(branch_a.id, summary="a1")
    await store.create_version(branch_a.id, summary="a2")
    b1 = await store.create_version(branch_b.id, summary="b1")

    assert b1.seq == 1, "不同分支的 seq 应各自独立"
    assert len(await store.list_versions(branch_a.id)) == 2


# ============================================================ 路书
async def test_save_and_read_plan_with_citations(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    version = await store.create_version(branch.id, summary="标准版")

    searched = datetime(2026, 10, 11, 15, 32)
    citations = [
        Citation(
            index=1,
            title="重庆攻略",
            url="https://example.com/a",
            provider="tavily",
            retrieved_at=searched,
        )
    ]
    await store.save_plan(
        version.id, markdown="# 重庆 3 天", citations=citations, searched_at=searched
    )

    detail = await store.get_version_detail(version.id)

    assert detail is not None
    assert detail.markdown == "# 重庆 3 天"
    assert detail.citations[0].url == "https://example.com/a"  # URL 原样保存
    assert detail.searched_at == searched


async def test_save_plan_upserts(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    version = await store.create_version(branch.id)

    await store.save_plan(version.id, markdown="第一版")
    await store.save_plan(version.id, markdown="第二版")

    detail = await store.get_version_detail(version.id)

    assert detail is not None
    assert detail.markdown == "第二版"


async def test_missing_plan_returns_empty_detail(store: SessionStore) -> None:
    session = await store.create_session("重庆之行")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    version = await store.create_version(branch.id)

    detail = await store.get_version_detail(version.id)

    assert detail is not None
    assert detail.markdown == ""
    assert detail.citations == []
