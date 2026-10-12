"""节点一：需求收集（collector）。

对照阶段①的改进：
- 旧项目手工 ``json.loads(res.content.strip("```json"))`` + 裸 ``except`` + ``data["status"]``
  直接下标 → 此处改用 **Pydantic 结构化输出**，字段缺失由框架保证（P1-2 / P1-3 / P1-4）；
- ``ready`` 由**程序**依据必填字段是否齐全判定，不完全采信模型自述。
"""

from __future__ import annotations

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

logger = get_logger(__name__)


class CollectResult(BaseModel):
    """collector 的结构化输出契约。"""

    extracted: Requirements = Field(default_factory=Requirements)
    missing_fields: list[str] = Field(default_factory=list)
    reply: str = Field(description="给用户的自然语言回复，不超过两句话")
    ready: bool = Field(default=False, description="模型自评：必填项是否齐全")


def make_collector_node(llm: LLMProvider):
    """构造 collector 节点。"""

    async def collector(state: PlanState) -> dict:
        emit_progress("collect")
        current = state.get("requirements") or Requirements()
        user_text = last_user_text(state)

        prompt = render_prompt(
            "collect",
            collected_json=current.model_dump_json(exclude_none=True, exclude_defaults=True),
        )
        result = await llm.structured(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(role="user", content=user_text or "（用户尚未输入内容）"),
            ],
            CollectResult,
        )

        merged = current.merged_with(result.extracted)
        missing = merged.missing_fields()
        ready = not missing  # ★ 由程序判定，不依赖模型自述

        reply = result.reply.strip() or "请告诉我更多出行信息。"
        if ready:
            reply += "\n（信息收集完毕，正在为您检索交通、景点与避雷信息…）"
            logger.info("需求收集完成: %s", merged.model_dump(exclude_none=True))
        else:
            logger.info("需求仍缺失: %s", missing)

        return {
            "requirements": merged,
            "missing_fields": missing,
            "messages": [AIMessage(content=reply)],
            "status": PlanStatus.RESEARCHING if ready else PlanStatus.COLLECTING,
            "updated_at": datetime.now(),
        }

    return collector
