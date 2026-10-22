"""OpenAI 兼容协议的 LLM 实现。

覆盖范围（规划 §6.1）：

* **云端**：DeepSeek / OpenAI / 月之暗面 / 智谱 / 通义 / 硅基流动 / OpenRouter …
* **本地**：Ollama / vLLM / LM Studio / llama.cpp server / Xinference …

它们都暴露 ``/v1`` 形态的 OpenAI 兼容端点，因此**一个实现即可全部覆盖**，
新增服务商只需往注册表里加一条元数据。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.errors import (
    AppError,
    LLMAuthenticationFailed,
    LLMOutputInvalid,
    LLMTimeout,
    LLMUnavailable,
)
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


def _with_json_hint(messages: Sequence[ChatMessage], schema: type[BaseModel]) -> list[ChatMessage]:
    """为 json_object 模式补上「请输出 JSON」的指令。

    LangChain 的 ``method="json_mode"`` 要求提示中必须出现 "JSON" 字样，
    否则会直接抛错；这里同时把字段结构一并给出，提高解析成功率。
    """
    schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    hint = (
        "\n\n请只输出一个 JSON 对象，不要 markdown 代码块、不要任何额外说明文字。"
        f"字段结构需符合：{schema_text}"
    )

    updated = list(messages)
    if updated and updated[-1].role == "user":
        updated[-1] = ChatMessage(role="user", content=updated[-1].content + hint)
    else:
        updated.append(ChatMessage(role="user", content=hint.strip()))
    return updated


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

    教训：早期版本把所有 ``structured=True`` 的异常都归为
    ``LLMOutputInvalid``（「模型返回的内容格式异常」），
    结果把「该模型不支持 json_schema 参数」这类 API 错误
    伪装成了「模型输出有问题」，严重误导排查方向。

    现在按**真实原因**分类；技术细节放进 ``detail``（只进日志，不进响应体）。
    """
    if isinstance(exc, AppError):
        return exc

    name = type(exc).__name__.lower()
    text = str(exc)
    lowered = text.lower()
    detail = f"{type(exc).__name__}: {text[:500]}"

    if "timeout" in name or "timed out" in lowered:
        return LLMTimeout(detail=detail)

    # ---- 鉴权 ----
    if "authentication" in name or "401" in text or "invalid api key" in lowered:
        return LLMAuthenticationFailed()

    # ---- 模型不存在 / 名称写错 ----
    if (
        "notfound" in name
        or "404" in text
        or "model not found" in lowered
        or "does not exist" in lowered
    ):
        return LLMUnavailable("模型不存在，请检查模型名称是否正确", detail=detail)

    # ---- 限流 ----
    if "ratelimit" in name or "429" in text or "rate limit" in lowered:
        return LLMUnavailable("模型服务请求过于频繁，请稍后重试", detail=detail)

    # ---- 明确不支持结构化输出（这是「参数不被支持」，不是「模型输出有问题」）----
    if "response_format" in lowered or "json_schema" in lowered:
        return LLMOutputInvalid("当前模型不支持所选的结构化输出格式，请更换模型", detail=detail)

    # ---- 其它 4xx：请求被拒 ----
    if "badrequest" in name or "400" in text or "invalid_request" in lowered:
        return LLMUnavailable("模型服务拒绝了本次请求，请检查模型名称与参数", detail=detail)

    # ---- 结构化解析失败（确实不是合法 JSON / 不符合 schema）----
    if structured and ("validation" in name or "parse" in name or "json" in lowered):
        return LLMOutputInvalid(detail=detail)

    if structured:
        return LLMOutputInvalid(detail=detail)
    return LLMUnavailable(detail=detail)


def _supports_json_mode_fallback(exc: Exception) -> bool:
    """判断是否属于「模型不支持 json_schema，可退回 json_object」的情形。

    DeepSeek 当前就会返回：
        This response_format type is unavailable now
    """
    lowered = str(exc).lower()
    if "response_format" not in lowered and "json_schema" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in ("unavailable", "not support", "unsupported", "invalid_request_error")
    )


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
        """取结构化结果，并在模型不支持时**自动降级**。

        背景（真实踩到）：``with_structured_output`` 默认用
        ``response_format: json_schema``，但不少服务（如 DeepSeek）尚未支持，
        会直接返回 400。若不降级，用户看到的是「模型返回格式异常」这种误导性提示。

        策略：先试 ``json_schema``（结构化最可靠），失败则退回 ``json_object``。
        """
        # ---- 首选：json_schema ----
        try:
            runnable = self._build(temperature).with_structured_output(schema)
            result = await runnable.ainvoke(_to_langchain(messages))
            return self._coerce(result, schema)
        except Exception as exc:
            mapped = _map_error(exc, structured=True)
            parsing_failed = isinstance(mapped, LLMOutputInvalid) and (
                isinstance(exc, LLMOutputInvalid)
                or any(word in type(exc).__name__.lower() for word in ("parse", "validation"))
            )
            if not parsing_failed and not _supports_json_mode_fallback(exc):
                raise _map_error(exc, structured=True) from exc
            logger.info(
                "结构化输出切换为 JSON 模式并自动重试一次",
                extra={"extra_fields": {"model": self.model}},
            )

        # ---- 降级：json_object（需在提示中显式要求 JSON）----
        try:
            fallback = self._build(temperature).with_structured_output(schema, method="json_mode")
            result = await fallback.ainvoke(_to_langchain(_with_json_hint(messages, schema)))
            return self._coerce(result, schema)
        except Exception as exc:
            raise _map_error(exc, structured=True) from exc

    @staticmethod
    def _coerce(result: object, schema: type[SchemaT]) -> SchemaT:
        if isinstance(result, schema):
            return result
        try:  # 少数实现会返回 dict
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

    async def list_models(self) -> list[str]:
        """列出服务商当前可用的模型。

        为什么需要：模型名会随官方调整（DeepSeek 就有过多次更名），
        让用户手写极易出错。此接口走标准的 ``GET /models``，
        兼容 OpenAI 与各家 OpenAI 兼容服务。
        """
        import httpx

        base = (self._base_url or "").rstrip("/")
        if not base:
            raise LLMUnavailable("未配置接口地址，无法获取模型列表")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{base}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except Exception as exc:
            raise LLMUnavailable(
                "无法连接到模型服务，请检查接口地址与网络", detail=str(exc)
            ) from exc

        if response.status_code in (401, 403):
            raise LLMUnavailable("获取模型列表失败：API Key 无效", detail=response.text[:200])
        if response.status_code >= 400:
            raise LLMUnavailable(
                f"该服务未提供模型列表接口（HTTP {response.status_code}），请手动填写模型名",
                detail=response.text[:200],
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise LLMUnavailable("模型列表返回格式异常", detail=str(exc)) from exc

        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise LLMUnavailable("该服务返回的模型列表结构不受支持，请手动填写模型名")

        models = sorted(
            str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")
        )
        if not models:
            raise LLMUnavailable("该服务未返回任何可用模型")
        return models
