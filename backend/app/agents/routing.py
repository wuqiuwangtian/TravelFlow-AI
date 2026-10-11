"""路由函数（规划 §8.2）。

**``route_entry`` 是 P0-1 的修复核心。**
旧项目入口固定为 ``START → collector``，所以第二轮用户说"确认"时
仍然会走需求收集流程，触发全量重搜与整份重生成。
这里改为**按会话状态决定入口**。
"""

from __future__ import annotations

from langgraph.graph import END

from app.agents.state import PlanState
from app.domain.enums import PlanStatus


def route_entry(state: PlanState) -> str:
    """按会话状态决定进入哪个节点（替代固定入口）。"""
    status = state.get("status", PlanStatus.COLLECTING)

    if status == PlanStatus.RESEARCHING:
        return "researcher"  # 中断后继续检索
    if status == PlanStatus.PLANNING:
        return "planner"  # 中断后继续生成

    if status in (PlanStatus.REVIEWING, PlanStatus.FINISHED):
        # ★ 待确认 / 已定稿状态下的用户输入走 refiner，而不是重新收集需求
        return "refiner"

    return "collector"


def route_after_collect(state: PlanState) -> str:
    """需求齐全就去检索，否则结束本轮、等待用户补充。"""
    return "researcher" if state.get("status") == PlanStatus.RESEARCHING else END


def route_after_plan(state: PlanState) -> str:
    """生成完路书即停下，等待用户反馈（人机交互断点）。"""
    return END


def route_after_refine(state: PlanState) -> str:
    """确认收尾或等待下一轮反馈。"""
    return END
