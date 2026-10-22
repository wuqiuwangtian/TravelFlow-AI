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

import re
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
from app.core.errors import InvalidRequest, SearchUnavailable
from app.core.logging import get_logger
from app.domain.enums import DetailLevel, PlanStatus
from app.domain.itinerary import ClockTime, ReplanInput
from app.domain.models import Requirements
from app.llm.base import ChatMessage, LLMProvider
from app.search.base import SearchProvider
from app.services.itinerary_service import enrich_itinerary, sync_markdown
from app.services.journey_messages import prepare_journey_message
from app.services.replan_service import explicit_day, set_replan_context
from app.services.travel_time import time_label

logger = get_logger(__name__)

APPROVE_REPLY = "太好了！计划已确认。你可以在右侧导出 Markdown 或 HTML 保存下来。"
UNCLEAR_REPLY = "请问您想调整哪一部分？例如「第二天轻松一点」「换一家酒店」「预算降到 1500」。"


class OnTripUpdate(BaseModel):
    day: int | None = Field(default=None, ge=1, le=60)
    location_name: str = Field(default="", max_length=100)
    city: str = Field(default="", max_length=50)
    time: ClockTime
    start_day_offset: int = Field(
        default=0, ge=0, le=60, description="出发时刻相对该行程日的天数偏移，当日0、次日1"
    )
    end_time: ClockTime | None = None
    end_day_offset: int = Field(
        default=0,
        ge=0,
        le=60,
        description="结束时刻相对该行程日的天数偏移，当日0、次日1；不擅自推断跨日",
    )
    end_location_name: str = Field(default="", max_length=100)


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
    on_trip: OnTripUpdate | None = Field(
        default=None,
        description="用户明确描述旅途中起点和出发时间时提取，未提及则为空；不猜测时间或地点。",
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
        batch = prepare_journey_message(user_text, current, requirements) if current else None
        if batch and not batch.refinement:
            updated_itinerary = batch.itinerary or current
            assert updated_itinerary is not None
            for changed_day in sorted(batch.booking_days):
                updated_itinerary = await enrich_itinerary(
                    updated_itinerary,
                    requirements,
                    settings or Settings(),
                    day_number=changed_day,
                )
            return {
                "itinerary": updated_itinerary,
                "plan_markdown": sync_markdown(plan_markdown, updated_itinerary, requirements),
                "messages": [AIMessage(content=batch.reply)],
                "revision": revision + (1 if batch.itinerary is not None else 0),
                "updated_at": datetime.now(),
            }
        if batch:
            # 多项操作只在内存草稿中执行，模型调整成功后随整份行程一起提交。
            current = batch.itinerary
            user_text = batch.refinement
            assert current is not None
            plan_markdown = sync_markdown(plan_markdown, current, requirements)

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

        if batch and verdict.intent != "modify":
            return {
                "messages": [
                    AIMessage(
                        content="本次没有保存任何操作：后续调整还不明确，请补充要改哪一天、哪些安排。"
                    )
                ],
                "updated_at": datetime.now(),
            }

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

        scope_day = None
        if current:
            scope_day = explicit_day(user_text)
            context_days = [d for d in current.days if d.replan_context]
            if verdict.on_trip:
                update = verdict.on_trip
                days_mentioned = set(
                    re.findall(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*天", user_text)
                )
                if (update.start_day_offset or update.end_day_offset) and not (
                    re.search(r"次日|翌日|明天|[\d一二三四五六七八九十两]+天后", user_text)
                    or len(days_mentioned) > 1
                ):
                    raise InvalidRequest(
                        "跨午夜请明确说明出发和结束日期，例如「当日23:00出发，次日01:00前结束」。"
                    )
                active_days = [
                    d.day
                    for d in current.days
                    if any(a.progress == "in_progress" for a in d.activities)
                ]
                scope_day = (
                    update.day or scope_day or (active_days[0] if len(active_days) == 1 else None)
                )
                if scope_day is None:
                    return {
                        "messages": [
                            AIMessage(
                                content=("本次没有保存任何操作。" if batch else "")
                                + "请说明现在是旅程第几天，"
                                "例如「第2天14:00，我在景山，18:00前结束」。"
                            )
                        ],
                        "updated_at": datetime.now(),
                    }
                working = current.model_copy(deep=True)
                day = next((d for d in working.days if d.day == scope_day), None)
                if day is None:
                    raise InvalidRequest("这一天不在当前行程中")
                end_matches = [
                    a
                    for a in day.activities
                    if update.end_location_name
                    and update.end_location_name in (a.title, a.place_name)
                ]
                if not end_matches and update.end_location_name in {"酒店", "住处", "住宿"}:
                    end_matches = [a for a in day.activities if a.kind == "hotel"]
                if update.end_location_name and len(end_matches) != 1:
                    raise InvalidRequest("结束地点还不能唯一确定，请说明计划中的具体地点名称。")
                set_replan_context(
                    working,
                    scope_day,
                    ReplanInput(
                        activity_id=(
                            day.replan_context.activity_id
                            if day.replan_context
                            else next(
                                (a.id for a in day.activities if a.progress == "in_progress"), None
                            )
                        )
                        if not update.location_name
                        else None,
                        location_name=update.location_name,
                        city=update.city or requirements.destination or "",
                        time=update.time,
                        start_day_offset=update.start_day_offset,
                        end_time=update.end_time,
                        end_day_offset=update.end_day_offset,
                        end_activity_id=end_matches[0].id if end_matches else None,
                    ),
                )
                current = working
            elif context_days and scope_day is None:
                latest = max(
                    context_days,
                    key=lambda d: d.replan_context.updated_at if d.replan_context else datetime.min,
                )
                scope_day = latest.day
            if scope_day is not None and not any(d.day == scope_day for d in current.days):
                scope_day = None

        # ---- 2. 更新需求，只有涉及新事实时补充检索 ----
        requirements = requirements.merged_with(verdict.requirements)
        changed = bool(verdict.requirements.model_dump(exclude_defaults=True, exclude_none=True))
        if changed and not verdict.on_trip and explicit_day(user_text) is None:
            scope_day = None
        if (verdict.needs_research or changed) and search:
            emit_notice("本次修改涉及新的出行信息，正在补充检索。")
            try:
                researched = await make_researcher_node(search, focus=user_text)(
                    {**state, "requirements": requirements}
                )
                fresh = researched["research"]
            except SearchUnavailable:
                if not research:
                    raise
                emit_notice("补充检索暂不可用，先按已有资料调整；新的营业时间和票价暂未核实。")
                fresh = None
            # 保持旧编号稳定，新资料追加到同一资料桶；防止新旧引用串号。
            if research and fresh:
                old = research.merged_results()
                combined = old + fresh.merged_results()
                research = fresh.model_copy(
                    update={"transport": combined, "attractions": [], "reviews": []}
                )
            elif fresh:
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
        current_itinerary = current
        if current_itinerary:
            actual = sum(e.amount_cents for e in current_itinerary.expenses) / 100
            prompt += (
                "\n【当前结构化行程，未要求修改的站点和字段必须保留】\n"
                + current_itinerary.model_dump_json(exclude={"expenses", "adjustments"})
            )
            if scope_day is not None:
                prompt += f"\n本次仅调整第{scope_day}天。起点之前的活动、已有预约和其他天保持原样。"
            prompt += (
                "\nreplan_context 是用户提供的出发地点、出发时刻与结束时限，后续行程必须从此接上；"
                "booking 是已有预约，不能改时间、地点、时长或遗漏。若时间不足优先删普通活动。"
                "is_context_anchor 标记的零停留出发位置由系统维护，无需作为新活动重复输出。"
                "start_day_offset 表示相对该行程日的日期：当日0、次日1。"
                "凌晨安排和跨日预约不能丢失偏移。"
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
        new_plan = plan_markdown if scope_day is not None else details.markdown
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
            previous=current,
            scope_day=scope_day,
        )
        reply = "已按您的意见调整，并更新每日行程与费用汇总。请再次查阅。"
        if itinerary and scope_day is not None:
            day = next(d for d in itinerary.days if d.day == scope_day)
            if day.replan_context:
                departure_label = time_label(
                    day.replan_context.time, day.replan_context.start_day_offset
                )
                reply = (
                    f"已从第{scope_day}天{departure_label}接上后续安排，保留已有预约和前段记录。"
                )
                if day.replan_context.omitted_titles:
                    reply += (
                        "为预留时间，精简了：" + "、".join(day.replan_context.omitted_titles) + "。"
                    )
                if any("冲突" in warning for warning in day.warnings):
                    reply += "仍有必要安排的时间冲突，原预约时间保持不变，详情已在时间轴提示。"
                if any("交通耗时尚未查到" in warning for warning in day.warnings):
                    reply += "部分交通时长未核实，仍需预留接驳时间。"
        return {
            "plan_markdown": new_plan,
            "itinerary": itinerary,
            "todos": details.todos,
            "requirements": requirements,
            "research": research,
            "status": PlanStatus.REVIEWING,
            "revision": revision + 1,
            "messages": [AIMessage(content=(batch.reply + "\n" if batch else "") + reply)],
            "updated_at": datetime.now(),
        }

    return refiner
