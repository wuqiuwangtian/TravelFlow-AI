"""并发与错误路径测试。

**回归阶段① P1-1**：旧项目在 ``async def`` 端点里调用同步的 ``app_graph.invoke()``，
实测「单请求 1.51s，2 个并发 3.01s」——完全串行，并发能力为 1。

这里验证新实现不会重蹈覆辙。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from pathlib import Path

import pytest

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.agents.nodes.planner import PlanOutput
from app.agents.nodes.refiner import RefineIntent
from app.core.errors import LLMUnavailable
from app.domain.enums import PlanStatus
from app.services.chat_service import ChatService
from app.storage.database import open_database
from app.storage.session_store import SessionStore
from tests.fakes import FakeLLM, FakeSearch, make_collect_result

REQUIREMENTS = {"destination": "重庆", "origin": "北京", "duration_days": 3, "budget": 2000}
MESSAGE = "我想去重庆玩3天，从北京出发，预算2000"


class Harness:
    """把「库 + 存储 + 图」打包，统一用 AsyncExitStack 管理生命周期。"""

    def __init__(self, stack: AsyncExitStack, store: SessionStore, graph, session, branch) -> None:
        self.stack = stack
        self.store = store
        self.graph = graph
        self.session = session
        self.branch = branch

    async def aclose(self) -> None:
        await self.stack.aclose()

    def service(self) -> ChatService:
        return ChatService(graph=self.graph, store=self.store)


async def _prepare(tmp_path: Path, *, llm, search) -> Harness:
    stack = AsyncExitStack()
    conn = await stack.enter_async_context(open_database(tmp_path / "sessions.db"))
    store = SessionStore(conn)
    session = await store.create_session("重庆之行")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    checkpointer = await stack.enter_async_context(open_checkpointer(tmp_path / "cp.db"))
    graph = build_graph(llm, search, checkpointer=checkpointer)
    return Harness(stack, store, graph, session, branch)


# ============================================================ 并发
async def test_concurrent_requests_are_not_serialized(tmp_path: Path) -> None:
    """两个分支并发时总耗时应接近单次，而不是两次相加。"""
    delay = 0.25
    harness = await _prepare(
        tmp_path,
        llm=FakeLLM(
            collect=make_collect_result(**REQUIREMENTS),
            plan=PlanOutput(markdown="# 路书"),
            # 第二次调用时分支已处于 reviewing，会走 refiner
            intent=RefineIntent(intent="modify", summary="调整"),
            delay=delay,
        ),
        search=FakeSearch(delay=delay),
    )

    try:
        service = harness.service()

        started = time.perf_counter()
        single = await service.send(
            session_id=harness.session.id, branch_id=harness.branch.id, message=MESSAGE
        )
        one_shot = time.perf_counter() - started
        assert single.status == PlanStatus.REVIEWING

        branch_b = await harness.store.create_branch(harness.session.id, "并发B")

        started = time.perf_counter()
        await asyncio.gather(
            service.send(
                session_id=harness.session.id, branch_id=harness.branch.id, message="再去"
            ),
            service.send(session_id=harness.session.id, branch_id=branch_b.id, message="换个"),
        )
        concurrent = time.perf_counter() - started

        assert concurrent < one_shot * 1.8, (
            f"疑似被串行化：单次 {one_shot:.2f}s，两次并发 {concurrent:.2f}s"
        )
    finally:
        await harness.aclose()


async def test_event_loop_is_not_blocked_during_chat(tmp_path: Path) -> None:
    """对话进行时事件循环仍能调度其它协程（旧项目此处会被完全阻塞）。"""
    harness = await _prepare(
        tmp_path,
        llm=FakeLLM(
            collect=make_collect_result(**REQUIREMENTS),
            plan=PlanOutput(markdown="# 路书"),
            delay=0.2,
        ),
        search=FakeSearch(delay=0.2),
    )

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.05)
            ticks += 1

    try:
        await asyncio.gather(
            harness.service().send(
                session_id=harness.session.id, branch_id=harness.branch.id, message=MESSAGE
            ),
            heartbeat(),
        )
        assert ticks >= 15, f"事件循环被阻塞，心跳只跳了 {ticks} 次"
    finally:
        await harness.aclose()


# ============================================================ 错误路径
async def test_llm_failure_surfaces_as_actionable_error(tmp_path: Path) -> None:
    """模型不可用时应抛领域异常，而不是 500 内部错误。"""

    class BrokenLLM(FakeLLM):
        async def structured(self, messages, schema, *, temperature=None):  # type: ignore[override]
            raise LLMUnavailable(detail="connection refused")

    harness = await _prepare(
        tmp_path, llm=BrokenLLM(collect=make_collect_result(**REQUIREMENTS)), search=FakeSearch()
    )

    try:
        with pytest.raises(LLMUnavailable) as exc_info:
            await harness.service().send(
                session_id=harness.session.id, branch_id=harness.branch.id, message=MESSAGE
            )

        assert exc_info.value.code == "LLM_UNAVAILABLE"
        assert exc_info.value.message
    finally:
        await harness.aclose()


async def test_failed_turn_does_not_record_version(tmp_path: Path) -> None:
    """失败的一轮不应留下半成品版本（否则版本树会出现空白项）。"""

    class BrokenLLM(FakeLLM):
        async def structured(self, messages, schema, *, temperature=None):  # type: ignore[override]
            raise LLMUnavailable(detail="boom")

    harness = await _prepare(
        tmp_path, llm=BrokenLLM(collect=make_collect_result(**REQUIREMENTS)), search=FakeSearch()
    )

    try:
        with pytest.raises(LLMUnavailable):
            await harness.service().send(
                session_id=harness.session.id, branch_id=harness.branch.id, message=MESSAGE
            )

        assert await harness.store.list_versions(harness.branch.id) == []
    finally:
        await harness.aclose()


# ============================================================ 分支隔离
async def test_branches_do_not_interfere(tmp_path: Path) -> None:
    """两个分支交替使用不应串味。"""
    harness = await _prepare(
        tmp_path,
        llm=FakeLLM(
            collect=make_collect_result(**REQUIREMENTS), plan=PlanOutput(markdown="# 路书")
        ),
        search=FakeSearch(),
    )

    try:
        branch_b = await harness.store.create_branch(harness.session.id, "分支B")
        service = harness.service()

        await service.send(
            session_id=harness.session.id, branch_id=harness.branch.id, message=MESSAGE
        )
        await service.send(session_id=harness.session.id, branch_id=branch_b.id, message=MESSAGE)

        versions_a = await harness.store.list_versions(harness.branch.id)
        versions_b = await harness.store.list_versions(branch_b.id)

        assert len(versions_a) == 1
        assert len(versions_b) == 1
        assert versions_a[0].id != versions_b[0].id
    finally:
        await harness.aclose()
