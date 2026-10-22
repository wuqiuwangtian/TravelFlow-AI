"""节点三：生成路书（planner）。

关键设计：
- 传给大模型的资料**只有编号与摘要，没有 URL**，
  模型因此没有任何机会改写或编造链接（规划 §19.3）；
- 同时产出结构化的「待办事项」（TodoItem），其官方渠道由程序匹配（规划 §19.5）。
"""

from __future__ import annotations

from datetime import datetime

from langchain_core.messages import AIMessage
from pydantic import Field

from app.agents.detail_levels import detail_name, detail_requirements, target_length_text
from app.agents.itinerary_output import PlanDetails, complete_details, prepare_itinerary
from app.agents.prompt_loader import render_prompt
from app.agents.state import PlanState
from app.agents.streaming import emit_progress, is_streaming
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import DetailLevel, PlanStatus
from app.domain.models import TodoItem
from app.llm.base import ChatMessage, LLMProvider

logger = get_logger(__name__)


class PlanOutput(PlanDetails):
    """planner 的结构化输出契约。"""

    markdown: str = Field(description="完整的 Markdown 路书")
    todos: list[TodoItem] = Field(
        default_factory=list,
        description="需要用户提前办理或自行核实的事项（不含任何网址）",
    )


def make_planner_node(llm: LLMProvider, settings: Settings | None = None):
    """构造 planner 节点。"""

    async def planner(state: PlanState) -> dict:
        emit_progress("plan")
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
            departure_date=str(requirements.departure_date or "日期未定"),
            budget_basis={"per_person": "人均", "total": "全团"}.get(
                requirements.budget_basis or "", "口径未明确"
            ),
            numbered_sources=numbered_sources,
            detail_name=detail_name(level),
            target_length=target_length_text(level, requirements.duration_days),
            detail_requirements=detail_requirements(level),
        )

        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(role="user", content="请生成路书。"),
        ]

        # 先生成结构化主数据，避免流式正文再抽取造成的信息丢失与重复。
        result = await llm.structured(messages, PlanOutput)
        markdown = result.markdown
        details = await complete_details(llm, result, requirements, numbered_sources)

        todos = details.todos
        itinerary, markdown = await prepare_itinerary(
            details,
            markdown,
            requirements,
            settings or Settings(),
            len(research.build_citations()) if research else 0,
            previous=state.get("itinerary"),
        )

        logger.info(
            "路书生成完成",
            extra={
                "extra_fields": {
                    "chars": len(markdown),
                    "todos": len(todos),
                    "level": level.value,
                    "streamed": is_streaming(),
                }
            },
        )

        summary = (
            f"计划已生成（{detail_name(level)}档，约 {len(markdown)} 字）。"
            "下方时间轴已串联每天的地点与交通。\n\n"
            "想改哪里直接告诉我，例如「第二天轻松些」「下午换室内活动」。"
        )

        return {
            "plan_markdown": markdown,
            "todos": todos,
            "itinerary": itinerary,
            "status": PlanStatus.REVIEWING,
            "messages": [AIMessage(content=summary)],
            "updated_at": datetime.now(),
        }

    return planner
