"""节点四：确认 / 修改（refiner）。

**这是阶段① P0-1 的核心修复点。**
旧项目 ``refiner`` 入度为 0，永远不会被执行，导致：
- 用户说"确认"仍会重新收集需求 → 全量重搜 + 整份重生成；
- 计划永远无法定稿。

本节点同时修复旧项目另两个问题：
- 用 ``"YES" in llm_output`` 判断确认（遇到 "NOT YES" 之类会误判）→ 改为**结构化枚举**；
- 修改时整份重生成 → 改为**最小必要修改**，并**复用已有检索结果（零额外检索）**。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from app.agents.detail_levels import detail_name, target_length_text
from app.agents.itinerary_output import PlanDetails, prepare_itinerary
from app.agents.nodes.researcher import make_researcher_node
from app.agents.nodes.utils import last_user_text
from app.agents.prompt_loader import render_prompt
from app.agents.state import PlanState
from app.agents.streaming import emit_notice, emit_progress
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import DetailLevel, PlanStatus
from app.domain.models import Requirements
from app.llm.base import ChatMessage, LLMProvider
from app.search.base import SearchProvider
from app.services.itinerary_service import sync_markdown
from app.services.travel_messages import handle_travel_message

logger = get_logger(__name__)

APPROVE_REPLY = "太好了！计划已确认。你可以在右侧导出 Markdown 或 HTML 保存下来。"
UNCLEAR_REPLY = "请问您想调整哪一部分？例如「第二天轻松一点」「换一家酒店」「预算降到 1500」。"


class RefineIntent(BaseModel):
    """意图判定结果 —— 用枚举替代字符串包含判断，消除误判。"""

    intent: Literal["approve", "modify", "unclear"]
    summary: str = Field(default="", description="一句话概括用户想要什么")
    requirements: Requirements = Field(
        default_factory=Requirements, description="只提取本次明确改变的出行需求"
    )
    needs_research: bool = Field(
        default=False,
        description="新增景点、酒店、修改目的地或日期等需要新资料时为 true，改措辞、顺序则为 false",
    )


class RevisionOutput(PlanDetails):
    markdown: str = Field(description="修改后的完整文字路书")


def make_refiner_node(
    llm: LLMProvider, settings: Settings | None = None, search: SearchProvider | None = None
):
    """构造 refiner 节点。"""

    async def refiner(state: PlanState) -> dict:
        emit_progress("refine")
        user_text = last_user_text(state)
        plan_markdown = state.get("plan_markdown", "")
        research = state.get("research")
        requirements = state["requirements"]
        level = state.get("detail_level") or DetailLevel.L2_STANDARD
        revision = int(state.get("revision", 0))

        current = state.get("itinerary")
        action = handle_travel_message(user_text, current, requirements) if current else None
        if action:
            updated_itinerary = action.itinerary or current
            assert updated_itinerary is not None
            return {
                "itinerary": updated_itinerary,
                "plan_markdown": sync_markdown(plan_markdown, updated_itinerary, requirements),
                "messages": [AIMessage(content=action.reply)],
                "revision": revision + (1 if action.itinerary is not None else 0),
                "updated_at": datetime.now(),
            }

        # ---- 1. 意图判定 ----
        verdict = await llm.structured(
            [
                ChatMessage(
                    role="system",
                    content=render_prompt("intent", user_message=user_text or "（空消息）"),
                )
            ],
            RefineIntent,
        )
        logger.info(
            "意图判定",
            extra={"extra_fields": {"intent": verdict.intent, "summary": verdict.summary[:40]}},
        )

        if verdict.intent == "approve":
            return {
                "status": PlanStatus.FINISHED,
                "messages": [AIMessage(content=APPROVE_REPLY)],
                "updated_at": datetime.now(),
            }

        if verdict.intent == "unclear":
            return {
                "status": PlanStatus.REVIEWING,
                "messages": [AIMessage(content=UNCLEAR_REPLY)],
                "updated_at": datetime.now(),
            }

        # ---- 2. 更新需求，只有涉及新事实时补充检索 ----
        requirements = requirements.merged_with(verdict.requirements)
        changed = bool(verdict.requirements.model_dump(exclude_defaults=True, exclude_none=True))
        if (verdict.needs_research or changed) and search:
            emit_notice("本次修改涉及新的出行信息，正在补充检索。")
            researched = await make_researcher_node(search, focus=user_text)(
                {**state, "requirements": requirements}
            )
            fresh = researched["research"]
            # 保持旧编号稳定，新资料追加到同一资料桶；防止新旧引用串号。
            if research:
                old = research.merged_results()
                combined = old + fresh.merged_results()
                research = fresh.model_copy(
                    update={"transport": combined, "attractions": [], "reviews": []}
                )
            else:
                research = fresh

        prompt = render_prompt(
            "refine",
            plan_markdown=plan_markdown or "（当前尚无路书）",
            numbered_sources=research.numbered_sources() if research else "（无检索结果）",
            user_message=user_text,
            detail_name=detail_name(level),
            target_length=target_length_text(level, requirements.duration_days),
        )
        prompt += "\n【更新后的需求】\n" + requirements.model_dump_json(exclude_none=True)
        current_itinerary = state.get("itinerary")
        if current_itinerary:
            actual = sum(e.amount_cents for e in current_itinerary.expenses) / 100
            prompt += (
                "\n【当前结构化行程，未要求修改的站点和字段必须保留】\n"
                + current_itinerary.model_dump_json(exclude={"expenses"})
            )
            prompt += (
                "\n已完成、进行中和已跳过的活动属于旅中历史，保持原样，只调整后续未开始的安排。"
                "实际消费由账本管理，不可改成预算或重复计入。"
                f"当前已记全团实付合计：{actual:.2f}元。"
            )
        prompt += (
            "\n返回完整 markdown 和 days。停留点是具体地点；抵达该点的交通放在 transport_* 字段中。"
            "建议时间可以调整，费用估算须标 estimate，不得编造班次或票面时间。"
            "局部修改必须保留其余天数、地点、费用来源、交通方式和待办。"
        )
        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(role="user", content="请按上述意见修改路书。"),
        ]
        details = await llm.structured(messages, RevisionOutput)
        new_plan = details.markdown
        if not details.days and state.get("itinerary"):
            # 不允许格式缺失的回答把现有可用线路变回空白。
            from app.core.errors import LLMOutputInvalid

            raise LLMOutputInvalid("本次调整未返回完整行程，原计划已保留，请重试。")
        itinerary, new_plan = await prepare_itinerary(
            details,
            new_plan,
            requirements,
            settings or Settings(),
            len(research.build_citations()) if research else 0,
            previous=state.get("itinerary"),
        )
        return {
            "plan_markdown": new_plan,
            "itinerary": itinerary,
            "todos": details.todos,
            "requirements": requirements,
            "research": research,
            "status": PlanStatus.REVIEWING,
            "revision": revision + 1,
            "messages": [
                AIMessage(content="已按您的意见调整，并更新每日行程与费用汇总。请再次查阅。")
            ],
            "updated_at": datetime.now(),
        }

    return refiner
