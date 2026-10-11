"""检索 Provider 工厂 —— 按配置构造，新增服务商不改调用方。"""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import ConfigError
from app.core.providers import SEARCH_PROVIDERS, find_search_provider
from app.search.base import SearchProvider
from app.search.bocha_provider import BochaProvider
from app.search.ddg_provider import DuckDuckGoProvider
from app.search.qianfan_provider import QianfanProvider
from app.search.searxng_provider import SearXNGProvider
from app.search.tavily_provider import TavilyProvider


def create_search_provider(settings: Settings) -> SearchProvider:
    """根据配置创建检索 Provider。"""
    provider_id = settings.search_provider
    meta = find_search_provider(provider_id)
    if meta is None:
        known = ", ".join(p.id for p in SEARCH_PROVIDERS)
        raise ConfigError(f"未知的检索服务「{provider_id}」，可选：{known}")

    timeout = settings.search_timeout_seconds

    if provider_id == "tavily":
        _require_key(settings, "Tavily", "SEARCH_API_KEY")
        return TavilyProvider(settings.search_api_key or "", timeout=timeout)

    if provider_id == "bocha":
        _require_key(settings, "博查 BochaAI", "SEARCH_API_KEY")
        return BochaProvider(settings.search_api_key or "", timeout=timeout)

    if provider_id == "qianfan":
        _require_key(settings, "百度千帆", "SEARCH_API_KEY")
        return QianfanProvider(settings.search_api_key or "", timeout=timeout)

    if provider_id == "searxng":
        if not settings.searxng_base_url:
            raise ConfigError(
                "使用自建 SearXNG 需要配置 SEARXNG_BASE_URL，例如 http://localhost:8080"
            )
        return SearXNGProvider(settings.searxng_base_url, timeout=timeout)

    if provider_id == "ddg":
        return DuckDuckGoProvider(timeout=timeout)

    raise ConfigError(f"检索服务「{provider_id}」尚未实现")  # pragma: no cover


def _require_key(settings: Settings, label: str, env_name: str) -> None:
    if not settings.search_api_key:
        raise ConfigError(
            f"未配置 {label} 的 API Key，请在 .env 中设置 {env_name}；"
            "或改用 SEARCH_PROVIDER=ddg（无需密钥）"
        )
