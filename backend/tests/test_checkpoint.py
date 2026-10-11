"""会话持久化测试 —— 验证断点续聊与状态隔离。"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage

from app.agents.checkpoint import open_checkpointer, thread_config
from app.agents.graph import build_graph
from app.agents.nodes.planner import PlanOutput
from app.agents.nodes.refiner import RefineIntent
from app.agents.state import initial_state
from app.domain.enums import PlanStatus
from tests.fakes import FakeLLM, FakeSearch, make_collect_result

REQUIREMENTS = {"destination": "重庆", "origin": "北京", "duration_days": 3, "budget": 2000}


def _fakes() -> tuple[FakeLLM, FakeSearch]:
    llm = FakeLLM(
        collect=make_collect_result(**REQUIREMENTS),
        plan=PlanOutput(markdown="# 重庆路书"),
        intent=RefineIntent(intent="approve", summary="确认"),
    )
    return llm, FakeSearch()


async def test_state_survives_reopening_the_database(tmp_path: Path) -> None:
    """进程重启后仍能取到同一会话的状态（旧项目重启即丢）。"""
    db = tmp_path / "sessions.db"
    config = thread_config("branch-1")
    llm, search = _fakes()

    state = initial_state()
    state["messages"] = [HumanMessage(content="我想去重庆玩3天，从北京出发，预算2000")]

    # ---- 第一次运行 ----
    async with open_checkpointer(db) as saver:
        graph = build_graph(llm, search, checkpointer=saver)
        result = await graph.ainvoke(state, config)
        assert result["status"] == PlanStatus.REVIEWING

    # ---- 关闭后重新打开（模拟进程重启）----
    async with open_checkpointer(db) as saver:
        graph = build_graph(llm, search, checkpointer=saver)
        snapshot = await graph.aget_state(config)

    assert snapshot.values["status"] == PlanStatus.REVIEWING
    # 反序列化后仍是领域对象（这正是我们要的强类型）
    assert snapshot.values["requirements"].destination == "重庆"
    assert snapshot.values["plan_markdown"] == "# 重庆路书"


async def test_branches_are_isolated_by_thread(tmp_path: Path) -> None:
    """不同分支（thread_id）状态互不干扰 —— 分支对比功能的基础。"""
    db = tmp_path / "sessions.db"
    llm, search = _fakes()

    async with open_checkpointer(db) as saver:
        graph = build_graph(llm, search, checkpointer=saver)

        state_a = initial_state()
        state_a["messages"] = [HumanMessage(content="我想去重庆玩3天，从北京出发，预算2000")]
        await graph.ainvoke(state_a, thread_config("branch-A"))

        state_b = initial_state()
        state_b["messages"] = [HumanMessage(content="我想去成都玩5天，从上海出发，预算5000")]
        await graph.ainvoke(state_b, thread_config("branch-B"))

        snap_a = await graph.aget_state(thread_config("branch-A"))
        snap_b = await graph.aget_state(thread_config("branch-B"))

    # 两个分支的 thread_id 不同即视为不同会话；此处通过消息内容区分验证隔离
    assert snap_a.values["messages"][0].content != snap_b.values["messages"][0].content


async def test_checkpoint_history_is_available(tmp_path: Path) -> None:
    """历史快照可枚举 —— M4 的版本树依赖此能力。"""
    db = tmp_path / "sessions.db"
    llm, search = _fakes()
    config = thread_config("branch-1")

    state = initial_state()
    state["messages"] = [HumanMessage(content="我想去重庆玩3天，从北京出发，预算2000")]

    async with open_checkpointer(db) as saver:
        graph = build_graph(llm, search, checkpointer=saver)
        await graph.ainvoke(state, config)

        history = [snapshot async for snapshot in graph.aget_state_history(config)]

    assert len(history) >= 3  # collector / researcher / planner 各至少一步
