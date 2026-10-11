"""配置自检测试 —— 重点验证两件事：密钥不泄露、警告可操作。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SECRET_KEY = "sk-super-secret-should-never-leak"


@pytest.fixture
def client_with_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LLM_API_KEY", SECRET_KEY)
    monkeypatch.setenv("SEARCH_API_KEY", "tvly-also-secret")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def client_without_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_config_never_leaks_api_key(client_with_keys: TestClient) -> None:
    """安全底线：密钥绝不出现在响应体里。"""
    response = client_with_keys.get("/api/system/config")

    assert response.status_code == 200
    assert SECRET_KEY not in response.text
    assert "tvly-also-secret" not in response.text

    body = response.json()
    assert body["llm"]["has_key"] is True
    assert body["search"]["has_key"] is True


def test_missing_key_produces_actionable_warning(client_without_keys: TestClient) -> None:
    """警告必须是「人话 + 给出路」，而不是一句报错。"""
    body = client_without_keys.get("/api/system/config").json()

    assert body["llm"]["ready"] is False
    assert body["search"]["ready"] is False
    assert any("LLM_API_KEY" in w for w in body["warnings"])
    # 必须告诉用户可以换成本地模型
    assert any("ollama" in w for w in body["warnings"])
    # 必须告诉用户可以换免密钥检索
    assert any("ddg" in w for w in body["warnings"])


def test_local_llm_needs_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地模型不应被要求填 Key —— 这是「支持本地部署」的关键。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    get_settings.cache_clear()

    with TestClient(create_app()) as test_client:
        body = test_client.get("/api/system/config").json()

    get_settings.cache_clear()
    assert body["llm"]["ready"] is True
    assert not any("LLM_API_KEY" in w for w in body["warnings"])
    assert body["llm"]["base_url"] == "http://localhost:11434/v1"
