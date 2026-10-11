"""Agent 编排测试 —— 核心是回归阶段① P0-1（refiner 不可达）。

旧项目实测结果：
    第1轮 "去重庆3天"   → collector -> researcher -> planner
    第2轮 "确认，就这样" → collector -> researcher -> planner   ← refiner 从未出现
"""

from __future__ import annotations

import time

import pytest
from langchain_core.messages import HumanMessage

from app.agents.graph import build_graph
from app.agents.nodes.planner import PlanOutput
from app.agents.nodes.refiner import RefineIntent
from app.agents.routing import route_entry
from app.agents.state import initial_state
from app.core.errors import SearchUnavailable
from app.domain.enums import PlanStatus
from tests.fakes import FakeLLM, FakeSearch, make_collect_result

COMPLETE_REQUIREMENTS = {
    "destination": "重庆",
    "origin": "北京",
    "duration_days": 3,
    "budget": 2000,
}
FIRST_MESSAGE = "我想去重庆玩3天，从北京出发，预算2000"


def _new_graph(**llm_kwargs):
    """构造「一轮就能拿到路书」的图与状态。"""
    llm = FakeLLM(collect=make_collect_result(**COMPLETE_REQUIREMENTS), **llm_kwargs)
    search = FakeSearch()
    graph = build_graph(llm, search)
    state = initial_state()
    state["messages"] = [HumanMessage(content=FIRST_MESSAGE)]
    return llm, search, graph, state


async def _run_first_round(llm: FakeLLM, graph, state: dict) -> dict:
    return await graph.ainvoke(state)


# ============================================================ 入口路由（P0-1 的机制）
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PlanStatus.COLLECTING, "collector"),
        (PlanStatus.RESEARCHING, "researcher"),
        (PlanStatus.PLANNING, "planner"),
        (PlanStatus.REVIEWING, "refiner"),  # ★ 旧项目这条路由完全不存在
        (PlanStatus.FINISHED, "refiner"),
    ],
)
def test_route_entry_dispatches_by_status(status: PlanStatus, expected: str) -> None:
    assert route_entry({"status": status}) == expected


# ============================================================ 第一轮
async def test_first_round_runs_collect_research_plan() -> None:
    llm, search, graph, state = _new_graph(plan=PlanOutput(markdown="# 重庆 3 天路书"))

    out = await _run_first_round(llm, graph, state)

    assert out["status"] == PlanStatus.REVIEWING
    assert out["plan_markdown"].startswith("# 重庆")
    assert len(search.calls) == 3, "三路检索应并行发起"
    assert out["requirements"].destination == "重庆"
    assert not out["missing_fields"]


async def test_incomplete_requirements_stop_and_ask() -> None:
    llm = FakeLLM(collect=make_collect_result(destination="重庆"))
    search = FakeSearch()
    graph = build_graph(llm, search)

    state = initial_state()
    state["messages"] = [HumanMessage(content="我想去重庆")]

    out = await graph.ainvoke(state)

    assert out["status"] == PlanStatus.COLLECTING
    assert set(out["missing_fields"]) == {"origin", "duration_days", "budget"}
    assert search.calls == [], "信息不全时不应触发检索"


# ============================================================ ★ P0-1 回归
async def test_second_round_approve_goes_to_refiner_without_research() -> None:
    """第 2 轮「确认」必须走 refiner，且**不得重新检索**。"""
    llm, search, graph, state = _new_graph(plan=PlanOutput(markdown="# 重庆 3 天路书"))
    first = await _run_first_round(llm, graph, state)

    calls_after_first = len(search.calls)
    assert calls_after_first == 3

    llm._intent = RefineIntent(intent="approve", summary="用户确认")
    second_state = dict(first)
    second_state["messages"] = [*first["messages"], HumanMessage(content="确认，就这样")]
    second = await graph.ainvoke(second_state)

    assert second["status"] == PlanStatus.FINISHED
    # ★ 没有新增检索调用 → 证明没有退回 collector → researcher 的老路
    assert len(search.calls) == calls_after_first
    # ★ 也没有再次做需求抽取
    assert not any(call == "structured:CollectResult" for call in llm.calls[1:])


async def test_second_round_modify_only_refines_and_reuses_research() -> None:
    """第 2 轮「修改」只改内容，复用已有检索结果（零额外检索）。"""
    llm, search, graph, state = _new_graph(
        plan=PlanOutput(markdown="# 原路书"), refine_text="# 修改后的路书"
    )
    first = await _run_first_round(llm, graph, state)
    calls_before = len(search.calls)

    llm._intent = RefineIntent(intent="modify", summary="第二天轻松点")
    second_state = dict(first)
    second_state["messages"] = [*first["messages"], HumanMessage(content="第二天太赶了，轻松点")]
    second = await graph.ainvoke(second_state)

    assert second["status"] == PlanStatus.REVIEWING
    assert second["plan_markdown"] == "# 修改后的路书"
    assert second["revision"] == 1
    assert len(search.calls) == calls_before  # ★ 零额外检索


async def test_unclear_intent_asks_back() -> None:
    llm, search, graph, state = _new_graph(plan=PlanOutput(markdown="# 路书"))
    first = await _run_first_round(llm, graph, state)

    llm._intent = RefineIntent(intent="unclear", summary="无法判断")
    second_state = dict(first)
    second_state["messages"] = [*first["messages"], HumanMessage(content="嗯...")]
    second = await graph.ainvoke(second_state)

    assert second["status"] == PlanStatus.REVIEWING
    assert "调整哪一部分" in second["messages"][-1].content


# ============================================================ 检索容错
async def test_partial_search_failure_still_produces_plan() -> None:
    """单路检索失败不应拖垮整体（三路全挂才算失败）。"""
    llm = FakeLLM(
        collect=make_collect_result(**COMPLETE_REQUIREMENTS),
        plan=PlanOutput(markdown="# 路书"),
    )
    search = FakeSearch(fail_queries=("避雷",))
    graph = build_graph(llm, search)

    state = initial_state()
    state["messages"] = [HumanMessage(content=FIRST_MESSAGE)]
    out = await graph.ainvoke(state)

    assert out["status"] == PlanStatus.REVIEWING
    assert out["research"].reviews == []
    assert out["research"].attractions, "其他来源应仍然可用"


async def test_total_search_failure_raises() -> None:
    llm = FakeLLM(collect=make_collect_result(**COMPLETE_REQUIREMENTS))
    search = FakeSearch(fail_everything=True)
    graph = build_graph(llm, search)

    state = initial_state()
    state["messages"] = [HumanMessage(content=FIRST_MESSAGE)]

    with pytest.raises(SearchUnavailable):
        await graph.ainvoke(state)


# ============================================================ 并行检索（P1-6 回归）
async def test_research_runs_in_parallel_not_serial() -> None:
    """三路检索并行，总耗时应约等于单路耗时。"""
    llm = FakeLLM(
        collect=make_collect_result(**COMPLETE_REQUIREMENTS),
        plan=PlanOutput(markdown="# 路书"),
    )
    search = FakeSearch(delay=0.2)
    graph = build_graph(llm, search)

    state = initial_state()
    state["messages"] = [HumanMessage(content=FIRST_MESSAGE)]

    started = time.perf_counter()
    await graph.ainvoke(state)
    elapsed = time.perf_counter() - started

    # 串行需 ~0.6s，并行约 0.2s
    assert elapsed < 0.5, f"三路 0.2s 检索若串行需 0.6s，实测 {elapsed:.2f}s"
