"""OpenAI 兼容协议的 LLM 实现。

覆盖范围（规划 §6.1）：

* **云端**：DeepSeek / OpenAI / 月之暗面 / 智谱 / 通义 / 硅基流动 / OpenRouter …
* **本地**：Ollama / vLLM / LM Studio / llama.cpp server / Xinference …

它们都暴露 ``/v1`` 形态的 OpenAI 兼容端点，因此**一个实现即可全部覆盖**，
新增服务商只需往注册表里加一条元数据。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.errors import AppError, LLMOutputInvalid, LLMTimeout, LLMUnavailable
from app.core.logging import get_logger
from app.llm.base import ChatMessage, SchemaT

logger = get_logger(__name__)

_ROLE_TO_MESSAGE: dict[str, type[BaseMessage]] = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


def _to_langchain(messages: Sequence[ChatMessage]) -> list[BaseMessage]:
    return [_ROLE_TO_MESSAGE[m.role](content=m.content) for m in messages]


def _text_of(message: Any) -> str:
    """从 LangChain 消息对象里取出纯文本（兼容多模态内容块）。"""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content)


def _map_error(exc: Exception, *, structured: bool = False) -> AppError:
    """把底层异常映射为**面向用户**的领域异常。

    技术细节通过 ``detail`` 进入日志，不进入响应体（规划 §19 的可信度要求）。
    """
    if isinstance(exc, AppError):
        return exc

    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if "timeout" in name or "timed out" in text:
        return LLMTimeout(detail=f"{type(exc).__name__}: {exc}")
    if structured and ("validation" in name or "parse" in name):
        return LLMOutputInvalid(detail=f"{type(exc).__name__}: {exc}")
    if "authentication" in name or "unauthorized" in text or "invalid api key" in text:
        return LLMUnavailable(
            "模型服务鉴权失败，请检查 API Key 是否正确",
            detail=f"{type(exc).__name__}: {exc}",
        )
    if structured:
        return LLMOutputInvalid(detail=f"{type(exc).__name__}: {exc}")
    return LLMUnavailable(detail=f"{type(exc).__name__}: {exc}")


class OpenAICompatProvider:
    """基于 ``langchain-openai`` 的实现。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str | None,
        temperature: float = 0.7,
        timeout: int = 90,
        max_retries: int = 2,
        name: str = "openai-compat",
    ) -> None:
        self.name = name
        self.model = model
        # 本地模型通常不校验 Key，但客户端要求非空；给一个占位值
        self._api_key = api_key or "not-needed-for-local-model"
        self._base_url = base_url
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries

    def _build(self, temperature: float | None) -> ChatOpenAI:
        return ChatOpenAI(
            model=self.model,
            api_key=self._api_key,
            base_url=self._base_url,
            temperature=self._temperature if temperature is None else temperature,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
    ) -> str:
        try:
            response = await self._build(temperature).ainvoke(_to_langchain(messages))
        except Exception as exc:
            raise _map_error(exc) from exc
        return _text_of(response)

    async def structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        temperature: float | None = None,
    ) -> SchemaT:
        try:
            runnable = self._build(temperature).with_structured_output(schema)
            result = await runnable.ainvoke(_to_langchain(messages))
        except Exception as exc:
            raise _map_error(exc, structured=True) from exc

        if isinstance(result, schema):
            return result
        try:  # 少数情况下返回 dict
            return schema.model_validate(result)
        except Exception as exc:
            raise LLMOutputInvalid(detail=f"schema={schema.__name__} err={exc}") from exc

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        try:
            async for chunk in self._build(temperature).astream(_to_langchain(messages)):
                text = _text_of(chunk)
                if text:
                    yield text
        except Exception as exc:
            raise _map_error(exc) from exc

    async def health_check(self) -> tuple[bool, str]:
        """用一个极小的请求验证连通性与鉴权。"""
        try:
            await self.chat([ChatMessage(role="user", content="ping")], temperature=0)
        except AppError as exc:
            return False, exc.message
        except Exception as exc:  # pragma: no cover - 防御性
            return False, str(exc)
        return True, f"{self.name} / {self.model} 连接正常"
