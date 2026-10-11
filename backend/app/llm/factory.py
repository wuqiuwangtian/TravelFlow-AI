"""LLM Provider 工厂 —— 按配置构造实例。

因为所有目标服务都兼容 OpenAI 协议，这里其实只返回 ``OpenAICompatProvider``，
但保留了工厂这层，未来若要接入非 OpenAI 协议的模型（如 Anthropic 原生、Gemini 原生），
只需在 ``_BUILDERS`` 里加一条，调用方无感知。
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings
from app.core.errors import ConfigError
from app.core.providers import find_llm_provider
from app.llm.base import LLMProvider
from app.llm.openai_compat import OpenAICompatProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    """根据配置创建 LLM Provider。"""
    provider_id = settings.llm_provider
    meta = find_llm_provider(provider_id)
    if meta is None:
        known = ", ".join(p.id for p in _known_ids())
        raise ConfigError(f"未知的模型服务商「{provider_id}」，可选：{known}")

    if not settings.llm_ready:
        raise ConfigError(
            f"未配置 {provider_id} 的 API Key，请在 .env 中设置 LLM_API_KEY，"
            "或改用本地模型（LLM_PROVIDER=ollama）"
        )

    return OpenAICompatProvider(
        name=provider_id,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.resolved_llm_base_url,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _known_ids() -> list:
    from app.core.providers import LLM_PROVIDERS

    return list(LLM_PROVIDERS)


# 预留：非 OpenAI 协议的实现可注册于此
_BUILDERS: dict[str, Callable[[Settings], LLMProvider]] = {}
