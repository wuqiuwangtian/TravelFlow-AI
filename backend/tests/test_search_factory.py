"""检索 Provider 工厂测试 —— 每个服务商的构造与错误提示。"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ConfigError
from app.search.bocha_provider import BochaProvider
from app.search.ddg_provider import DuckDuckGoProvider
from app.search.factory import create_search_provider
from app.search.qianfan_provider import QianfanProvider
from app.search.searxng_provider import SearXNGProvider
from app.search.tavily_provider import TavilyProvider


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("provider_id", "expected_cls"),
    [
        ("tavily", TavilyProvider),
        ("bocha", BochaProvider),
        ("qianfan", QianfanProvider),
    ],
)
def test_key_based_providers_are_constructed(provider_id: str, expected_cls: type) -> None:
    provider = create_search_provider(
        _settings(search_provider=provider_id, search_api_key="test-key")
    )

    assert isinstance(provider, expected_cls)
    assert provider.name == provider_id


@pytest.mark.parametrize("provider_id", ["tavily", "bocha", "qianfan"])
def test_missing_key_gives_actionable_error(provider_id: str) -> None:
    with pytest.raises(ConfigError) as exc_info:
        create_search_provider(_settings(search_provider=provider_id, search_api_key=""))

    message = exc_info.value.message
    assert "SEARCH_API_KEY" in message
    assert "ddg" in message, "必须告诉用户还有免密钥的出路"


def test_ddg_needs_no_key() -> None:
    provider = create_search_provider(_settings(search_provider="ddg", search_api_key=""))

    assert isinstance(provider, DuckDuckGoProvider)
    assert provider.requires_api_key is False


def test_searxng_requires_base_url() -> None:
    with pytest.raises(ConfigError) as exc_info:
        create_search_provider(_settings(search_provider="searxng", searxng_base_url=""))

    assert "SEARXNG_BASE_URL" in exc_info.value.message


def test_searxng_is_constructed_with_url() -> None:
    provider = create_search_provider(
        _settings(search_provider="searxng", searxng_base_url="http://localhost:8080")
    )

    assert isinstance(provider, SearXNGProvider)
    assert provider.requires_api_key is False


def test_unknown_provider_lists_alternatives() -> None:
    with pytest.raises(ConfigError) as exc_info:
        create_search_provider(_settings(search_provider="not-a-provider"))

    message = exc_info.value.message
    for known in ("tavily", "bocha", "qianfan", "searxng", "ddg"):
        assert known in message


def test_timeout_is_passed_through() -> None:
    provider = create_search_provider(
        _settings(search_provider="tavily", search_api_key="k", search_timeout_seconds=7)
    )

    assert provider._timeout == 7  # type: ignore[attr-defined]
