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
from app.agents.nodes.utils import last_user_text
from app.agents.prompt_loader import render_prompt
from app.agents.state import PlanState
from app.agents.streaming import emit_progress, emit_token, is_streaming
from app.core.logging import get_logger
from app.domain.enums import DetailLevel, PlanStatus
from app.llm.base import ChatMessage, LLMProvider

logger = get_logger(__name__)

APPROVE_REPLY = "太好了！计划已确认。你可以在右侧导出 Markdown 或 HTML 保存下来。"
UNCLEAR_REPLY = "请问您想调整哪一部分？例如「第二天轻松一点」「换一家酒店」「预算降到 1500」。"


class RefineIntent(BaseModel):
    """意图判定结果 —— 用枚举替代字符串包含判断，消除误判。"""

    intent: Literal["approve", "modify", "unclear"]
    summary: str = Field(default="", description="一句话概括用户想要什么")


def make_refiner_node(llm: LLMProvider):
    """构造 refiner 节点。"""

    async def refiner(state: PlanState) -> dict:
        emit_progress("refine")
        user_text = last_user_text(state)
        plan_markdown = state.get("plan_markdown", "")
        research = state.get("research")
        requirements = state["requirements"]
        level = state.get("detail_level") or DetailLevel.L2_STANDARD
        revision = int(state.get("revision", 0))

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

        # ---- 2. 最小必要修改 ----
        # ★ 复用 state 中已有的 research，**不重新检索**
        prompt = render_prompt(
            "refine",
            plan_markdown=plan_markdown or "（当前尚无路书）",
            numbered_sources=research.numbered_sources() if research else "（无检索结果）",
            user_message=user_text,
            detail_name=detail_name(level),
            target_length=target_length_text(level, requirements.duration_days),
        )
        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(role="user", content="请按上述意见修改路书。"),
        ]
        if is_streaming():
            parts: list[str] = []
            async for piece in llm.stream(messages):
                parts.append(piece)
                emit_token(piece)
            new_plan = "".join(parts).strip()
        else:
            new_plan = await llm.chat(messages)

        return {
            "plan_markdown": new_plan,
            "status": PlanStatus.REVIEWING,
            "revision": revision + 1,
            "messages": [AIMessage(content="已按您的意见调整，其余内容保持不变。请再次查阅。")],
            "updated_at": datetime.now(),
        }

    return refiner
