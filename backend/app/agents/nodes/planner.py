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
from app.agents.streaming import emit_progress, emit_token, is_streaming
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


class TodoList(BaseModel):
    """只抽待办事项的结构化契约（流式模式下单独调用一次）。"""

    todos: list[TodoItem] = Field(
        default_factory=list,
        description="需要用户提前办理或自行核实的事项（不含任何网址）",
    )


_TODO_PROMPT = """从下面这份路书中，提取需要用户**提前办理或自行核实**的事项。

要求：
1. 每条包含：kind、what（要办什么）、when（建议什么时候办）、note（可留空）
   kind 只能是 railway / flight / ticket / reservation / hotel / other
2. **不要**输出任何网址或链接
3. 路书中没有这类事项就返回空列表

路书正文：
---
{markdown}
---"""


async def _extract_todos(llm: LLMProvider, markdown: str) -> list[TodoItem]:
    """从已生成的路书中抽取待办事项。

    流式模式下路书是逐字产出的、没有走结构化契约，
    所以这里补一次**轻量**的结构化调用专门拿待办 ——
    相比让用户丢掉「官方渠道」这个功能，多一次小调用的代价是值得的。
    """
    try:
        result = await llm.structured(
            [
                ChatMessage(role="system", content=_TODO_PROMPT.format(markdown=markdown[:8000])),
                ChatMessage(role="user", content="请提取。"),
            ],
            TodoList,
        )
        return result.todos
    except Exception as exc:
        # 抽取失败不应连累主流程：路书本身已经生成好了
        logger.warning("待办事项抽取失败（不影响路书）: %s", exc)
        return []


def make_planner_node(llm: LLMProvider):
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
            numbered_sources=numbered_sources,
            detail_name=detail_name(level),
            target_length=target_length_text(level, requirements.duration_days),
            detail_requirements=detail_requirements(level),
        )

        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(role="user", content="请生成路书。"),
        ]

        if is_streaming():
            # 流式：逐字推给前端，最后补一次轻量调用抽待办
            parts: list[str] = []
            async for piece in llm.stream(messages):
                parts.append(piece)
                emit_token(piece)
            markdown = "".join(parts).strip()
            todos = await _extract_todos(llm, markdown)
        else:
            result = await llm.structured(messages, PlanOutput)
            markdown, todos = result.markdown, result.todos

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
            "你可以在右侧查看详情。\n\n"
            "如果满意，请回复「确认」；需要调整（例如行程太赶、换个酒店、增减天数）直接告诉我。"
        )

        return {
            "plan_markdown": markdown,
            "todos": todos,
            "status": PlanStatus.REVIEWING,
            "messages": [AIMessage(content=summary)],
            "updated_at": datetime.now(),
        }

    return planner
