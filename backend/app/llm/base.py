"""大模型 Provider 抽象（规划 §6）。

设计目标：
1. **一个实现覆盖全部** —— 主流云模型与本地部署方案都提供 OpenAI 兼容接口，
   因此无需为每家写适配器，只要一个 ``OpenAICompatProvider`` + 一张注册表；
2. 所有调用**统一带超时与重试**（对照阶段①「外部调用无超时」缺陷）；
3. ``structured()`` 基于 Pydantic schema 取结构化结果，
   从机制上消除旧项目 ``data["status"]`` 直接下标导致的 KeyError（阶段① P1-2）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ChatMessage(BaseModel):
    """一条对话消息。"""

    role: Literal["system", "user", "assistant"]
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    """大模型提供方接口。"""

    name: str

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
    ) -> str:
        """返回纯文本回复。"""
        ...

    async def structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        temperature: float | None = None,
    ) -> SchemaT:
        """返回符合 ``schema`` 的结构化对象；不符合时抛 ``LLMOutputInvalid``。"""
        ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """流式返回增量文本。"""
        ...

    async def health_check(self) -> tuple[bool, str]:
        """连通性检查，返回 ``(是否可用, 说明)``。"""
        ...
