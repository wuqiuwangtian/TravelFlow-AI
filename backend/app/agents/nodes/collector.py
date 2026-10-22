"""节点一：需求收集（collector）。

对照阶段①的改进：
- 旧项目手工 ``json.loads(res.content.strip("```json"))`` + 裸 ``except`` + ``data["status"]``
  直接下标 → 此处改用 **Pydantic 结构化输出**，字段缺失由框架保证（P1-2 / P1-3 / P1-4）；
- ``ready`` 由**程序**依据必填字段是否齐全判定，不完全采信模型自述。
"""

from __future__ import annotations

import re
from datetime import datetime

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from app.agents.nodes.utils import last_user_text
from app.agents.prompt_loader import render_prompt
from app.agents.state import PlanState
from app.agents.streaming import emit_progress
from app.core.logging import get_logger
from app.domain.enums import PlanStatus
from app.domain.models import Requirements
from app.llm.base import ChatMessage, LLMProvider
from app.search.base import SearchProvider

logger = get_logger(__name__)


class CollectResult(BaseModel):
    """collector 的结构化输出契约。"""

    extracted: Requirements = Field(default_factory=Requirements)
    missing_fields: list[str] = Field(default_factory=list)
    reply: str = Field(description="给用户的自然语言回复，不超过两句话")
    ready: bool = Field(default=False, description="模型自评：必填项是否齐全")
    wants_destination_ideas: bool = Field(
        default=False, description="用户不知道去哪、希望推荐城市时为 true"
    )


class DestinationIdea(BaseModel):
    city: str
    reason: str


class DestinationOptions(BaseModel):
    ideas: list[DestinationIdea] = Field(default_factory=list, max_length=3)


def make_collector_node(llm: LLMProvider, search: SearchProvider | None = None):
    """构造 collector 节点。"""

    async def collector(state: PlanState) -> dict:
        emit_progress("collect")
        current = state.get("requirements") or Requirements()
        user_text = last_user_text(state)

        prompt = render_prompt(
            "collect",
            collected_json=current.model_dump_json(exclude_none=True, exclude_defaults=True),
        )
        if state.get("destination_ideas"):
            prompt += "\n【之前已给用户的城市候选，支持按序号选择】\n" + "、".join(
                state["destination_ideas"]
            )
        requested = bool(state.get("destination_recommendation_requested"))
        if requested:
            prompt += "\n用户已经要求推荐目的地，继续补齐必要条件后直接推荐，不要再问是否需要推荐。"
        history = [
            ChatMessage(role="user" if m.type == "human" else "assistant", content=m.content[:3000])
            for m in state.get("messages", [])[:-1][-10:]
            if m.type in {"human", "ai"} and isinstance(m.content, str)
        ]
        result = await llm.structured(
            [
                ChatMessage(role="system", content=prompt),
                *history,
                ChatMessage(role="user", content=user_text or "（用户尚未输入内容）"),
            ],
            CollectResult,
        )

        merged = current.merged_with(result.extracted)
        missing = merged.missing_fields()
        ready = not missing  # ★ 由程序判定，不依赖模型自述

        reply = result.reply.strip() or "请告诉我更多出行信息。"
        ideas = list(state.get("destination_ideas") or [])
        requested = (
            requested
            or result.wants_destination_ideas
            or bool(re.search(r"推荐|不知道去哪|去哪玩|哪里玩", user_text))
        )
        if re.search(r"不要推荐|不用推荐|不需要推荐", user_text):
            requested = False
        if not merged.destination and requested:
            labels = {"origin": "从哪里出发", "duration_days": "玩几天", "budget": "预算大约多少"}
            needed = [labels[name] for name in missing if name in labels]
            if needed:
                reply = "我会结合你的偏好推荐合适的目的地。还需要知道：" + "、".join(needed) + "。"
        if (
            not merged.destination
            and requested
            and not any(name in missing for name in ("origin", "duration_days", "budget"))
        ):
            emit_progress("collect", "正在根据出发地、时间与偏好寻找适合的城市…")
            evidence = ""
            if search:
                try:
                    results = await search.search(
                        f"{merged.origin}出发 {merged.duration_days}天 预算{merged.budget}元 "
                        f"{' '.join(merged.preferences)} 旅游城市推荐",
                        max_results=4,
                    )
                    evidence = "\n".join(f"{item.title}：{item.content[:600]}" for item in results)
                except Exception:
                    evidence = "检索暂不可用，仅按偏好提供灵感，不声称核实实时价格。"
            options = await llm.structured(
                [
                    ChatMessage(
                        role="system",
                        content="根据需求推荐最多三个适合的城市，说明各自玩法与取舍。仅提供灵感，不编造实时票价或声称完成可行性核实。用户选择后才进入具体行程。",
                    ),
                    ChatMessage(
                        role="user", content=merged.model_dump_json() + "\n参考资料：" + evidence
                    ),
                ],
                DestinationOptions,
            )
            ideas = [idea.city for idea in options.ideas]
            reply = (
                "可以先从这几个方向选：\n\n"
                + "\n\n".join(
                    f"{i}. {idea.city}：{idea.reason}" for i, idea in enumerate(options.ideas, 1)
                )
                + "\n\n告诉我更喜欢哪个，或说说想换一种什么玩法，我来继续规划。"
            )
        if ready:
            reply += "\n（信息收集完毕，正在为您检索交通、景点与避雷信息…）"
            logger.info("需求收集完成: %s", merged.model_dump(exclude_none=True))
        else:
            logger.info("需求仍缺失: %s", missing)

        return {
            "requirements": merged,
            "missing_fields": missing,
            "destination_ideas": ideas,
            "destination_recommendation_requested": requested and not bool(merged.destination),
            "messages": [AIMessage(content=reply)],
            "status": PlanStatus.RESEARCHING if ready else PlanStatus.COLLECTING,
            "updated_at": datetime.now(),
        }

    return collector
