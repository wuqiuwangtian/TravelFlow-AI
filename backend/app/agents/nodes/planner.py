"""节点三：生成路书（planner）。

关键设计：
- 传给大模型的资料**只有编号与摘要，没有 URL**，
  模型因此没有任何机会改写或编造链接（规划 §19.3）；
- 同时产出结构化的「待办事项」（TodoItem），其官方渠道由程序匹配（规划 §19.5）。
"""

from __future__ import annotations

from datetime import datetime

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from app.agents.detail_levels import detail_name, detail_requirements, target_length_text
from app.agents.prompt_loader import render_prompt
from app.agents.state import PlanState
from app.core.logging import get_logger
from app.domain.enums import DetailLevel, PlanStatus
from app.domain.models import TodoItem
from app.llm.base import ChatMessage, LLMProvider

logger = get_logger(__name__)


class PlanOutput(BaseModel):
    """planner 的结构化输出契约。"""

    markdown: str = Field(description="完整的 Markdown 路书")
    todos: list[TodoItem] = Field(
        default_factory=list,
        description="需要用户提前办理或自行核实的事项（不含任何网址）",
    )


def make_planner_node(llm: LLMProvider):
    """构造 planner 节点。"""

    async def planner(state: PlanState) -> dict:
        requirements = state["requirements"]
        research = state.get("research")
        level = state.get("detail_level") or DetailLevel.L2_STANDARD

        numbered_sources = research.numbered_sources() if research else "（无检索结果）"
        prompt = render_prompt(
            "plan",
            destination=requirements.destination or "（未提供）",
            origin=requirements.origin or "（未提供）",
            duration_days=requirements.duration_days or 1,
            budget=requirements.budget or "（未提供）",
            travelers=requirements.travelers or "未说明",
            preferences="、".join(requirements.preferences) or "无特别偏好",
            numbered_sources=numbered_sources,
            detail_name=detail_name(level),
            target_length=target_length_text(level, requirements.duration_days),
            detail_requirements=detail_requirements(level),
        )

        result = await llm.structured(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(role="user", content="请生成路书。"),
            ],
            PlanOutput,
        )

        logger.info(
            "路书生成完成",
            extra={
                "extra_fields": {
                    "chars": len(result.markdown),
                    "todos": len(result.todos),
                    "level": level.value,
                }
            },
        )

        summary = (
            f"计划已生成（{detail_name(level)}档，约 {len(result.markdown)} 字）。"
            "你可以在右侧查看详情。\n\n"
            "如果满意，请回复「确认」；需要调整（例如行程太赶、换个酒店、增减天数）直接告诉我。"
        )

        return {
            "plan_markdown": result.markdown,
            "todos": result.todos,
            "status": PlanStatus.REVIEWING,
            "messages": [AIMessage(content=summary)],
            "updated_at": datetime.now(),
        }

    return planner
