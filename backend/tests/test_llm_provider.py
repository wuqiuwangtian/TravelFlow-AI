"""LLM Provider 工厂与错误映射测试（不发起真实网络请求）。"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ConfigError, LLMOutputInvalid, LLMTimeout, LLMUnavailable
from app.llm.factory import create_llm_provider
from app.llm.openai_compat import _map_error


def _settings(**env: str) -> Settings:
    """构造一个不读取 .env 的干净配置。"""
    base = {
        "llm_provider": "deepseek",
        "llm_model": "deepseek-chat",
        "llm_api_key": "",
        "search_provider": "tavily",
        "search_api_key": "",
    }
    return Settings(_env_file=None, **{**base, **env})  # type: ignore[arg-type]


def test_cloud_provider_without_key_is_rejected() -> None:
    """云端模型缺 Key 时必须给出**可操作**的报错。"""
    with pytest.raises(ConfigError) as exc_info:
        create_llm_provider(_settings())

    message = exc_info.value.message
    assert "LLM_API_KEY" in message
    assert "ollama" in message  # 告诉用户出路：换本地模型


def test_local_provider_needs_no_key() -> None:
    """本地模型不应被要求填 Key —— 这是「支持本地部署」的关键。"""
    provider = create_llm_provider(_settings(llm_provider="ollama", llm_model="qwen2.5:7b"))

    assert provider.name == "ollama"
    assert provider._base_url == "http://localhost:11434/v1"  # type: ignore[attr-defined]


def test_unknown_provider_lists_alternatives() -> None:
    with pytest.raises(ConfigError) as exc_info:
        create_llm_provider(_settings(llm_provider="not-exist", llm_api_key="sk-x"))

    assert "deepseek" in exc_info.value.message


def test_explicit_base_url_overrides_preset() -> None:
    provider = create_llm_provider(
        _settings(llm_provider="custom", llm_base_url="http://192.168.1.9:8000/v1")
    )

    assert provider._base_url == "http://192.168.1.9:8000/v1"  # type: ignore[attr-defined]


# ---------------------------------------------------------------- 错误映射
def test_timeout_maps_to_llm_timeout() -> None:
    assert isinstance(_map_error(TimeoutError("request timed out")), LLMTimeout)


def test_auth_error_gives_actionable_message() -> None:
    class AuthenticationError(Exception):
        pass

    mapped = _map_error(AuthenticationError("Invalid API key"))

    assert isinstance(mapped, LLMUnavailable)
    assert "API Key" in mapped.message


def test_structured_parse_failure_maps_to_output_invalid() -> None:
    class ValidationError(Exception):
        pass

    assert isinstance(_map_error(ValidationError("bad schema"), structured=True), LLMOutputInvalid)


def test_connection_error_maps_to_unavailable() -> None:
    class APIConnectionError(Exception):
        pass

    assert isinstance(_map_error(APIConnectionError("boom")), LLMUnavailable)
