"""图装配（规划 §8.2）—— 只做拓扑，不含业务逻辑。

拓扑::

    START ─(route_entry 按状态分流)─┬─▶ collector ─(齐全?)─▶ researcher ─▶ planner ─▶ END
                                    ├─▶ researcher（中断续跑）
                                    ├─▶ planner（中断续跑）
                                    └─▶ refiner ─▶ END

与旧项目的关键差异：``refiner`` **有入边**（经 ``route_entry`` 可达）。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.collector import make_collector_node
from app.agents.nodes.planner import make_planner_node
from app.agents.nodes.refiner import make_refiner_node
from app.agents.nodes.researcher import make_researcher_node
from app.agents.routing import (
    route_after_collect,
    route_after_refine,
    route_entry,
)
from app.agents.state import PlanState
from app.llm.base import LLMProvider
from app.search.base import SearchProvider


def build_graph(
    llm: LLMProvider,
    search: SearchProvider,
    *,
    checkpointer: Any = None,
):
    """构建并编译状态图。

    ``checkpointer`` 传入 ``AsyncSqliteSaver`` 即可获得持久化与断点续聊能力。
    """
    workflow = StateGraph(PlanState)

    workflow.add_node("collector", make_collector_node(llm))
    workflow.add_node("researcher", make_researcher_node(search))
    workflow.add_node("planner", make_planner_node(llm))
    workflow.add_node("refiner", make_refiner_node(llm))

    # ★ 显式入口路由：按状态决定入口（旧项目固定 START → collector 的修复）
    workflow.add_conditional_edges(
        START,
        route_entry,
        {
            "collector": "collector",
            "researcher": "researcher",
            "planner": "planner",
            "refiner": "refiner",
        },
    )

    workflow.add_conditional_edges(
        "collector", route_after_collect, {"researcher": "researcher", END: END}
    )
    workflow.add_edge("researcher", "planner")
    workflow.add_edge("planner", END)
    workflow.add_conditional_edges("refiner", route_after_refine, {END: END})

    return workflow.compile(checkpointer=checkpointer)
