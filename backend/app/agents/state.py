"""LangGraph 状态定义（规划 §8.3）。

对照阶段①缺陷 P1-7：旧项目状态里 ``messages`` 声明为 ``List[dict]``，
但节点内按对象属性访问 ``.content``，server 层又要 ``hasattr`` 兼容两种形态。
此处消息统一为 ``BaseMessage``，其余字段全部强类型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.domain.clarification import JourneyClarification, PlaceClarification
from app.domain.enums import DetailLevel, PlanStatus
from app.domain.itinerary import Itinerary
from app.domain.linked_replan import LinkedReplanPreview, MoveClarification
from app.domain.models import Requirements, ResearchBundle, TodoItem


class PlanState(TypedDict, total=False):
    """会话状态。

    ``total=False`` 允许只初始化关心的字段，由节点逐步填充。
    """

    # 对话历史（add_messages 负责追加与类型归一）
    messages: Annotated[list[BaseMessage], add_messages]

    # 结构化需求
    requirements: Requirements

    # 检索产物（含引用）；refiner 复用此字段，不重新检索
    research: ResearchBundle | None

    # 当前路书
    plan_markdown: str
    todos: list[TodoItem]
    itinerary: Itinerary | None
    pending_journey: JourneyClarification | None
    pending_place: PlaceClarification | None
    pending_linked: LinkedReplanPreview | None
    pending_move: MoveClarification | None
    chat_request: dict[str, str] | None
    completed_chat: dict[str, str] | None
    completed_plan_undo: dict | None

    # 状态机
    status: PlanStatus
    missing_fields: list[str]
    destination_ideas: list[str]
    destination_recommendation_requested: bool

    # 生成偏好
    detail_level: DetailLevel

    # 元信息
    revision: int
    trace_id: str
    updated_at: datetime


def initial_state(
    *,
    detail_level: DetailLevel = DetailLevel.L2_STANDARD,
    trace_id: str = "-",
) -> PlanState:
    """构造一个全新的会话状态。"""
    return PlanState(
        messages=[],
        requirements=Requirements(),
        research=None,
        plan_markdown="",
        todos=[],
        itinerary=None,
        status=PlanStatus.COLLECTING,
        missing_fields=list(Requirements.REQUIRED_FIELDS),
        detail_level=detail_level,
        revision=0,
        trace_id=trace_id,
        updated_at=datetime.now(),
    )
