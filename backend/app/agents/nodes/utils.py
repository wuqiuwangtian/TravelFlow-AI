"""节点内部共用的小工具。"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

from app.agents.state import PlanState


def last_user_text(state: PlanState) -> str:
    """取出最近一条用户消息的文本。

    对照阶段①缺陷 P1-7：旧项目里 ``messages`` 混用 dict 与 BaseMessage，
    取值方式不统一。这里只走一条路径：``BaseMessage``。
    """
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) or _type_of(message) == "human":
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _type_of(message: object) -> str:
    if isinstance(message, BaseMessage):
        return message.type
    if isinstance(message, dict):
        return str(message.get("role") or message.get("type") or "")
    return ""
