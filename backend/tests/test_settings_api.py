"""设置读写接口测试。

两条底线：
1. **密钥永不回显**（GET 只给 has_key）；
2. **只能改白名单字段**，且不会写到测试之外的文件。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SECRET = "sk-must-never-appear-in-response"


@pytest.fixture
def client_with_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """把 .env 指向临时文件，避免污染真实配置。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 用户自己的注释\nLLM_PROVIDER=deepseek\nLLM_MODEL=deepseek-chat\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV_FILE", str(env_file))
    monkeypatch.setenv("SESSION_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    get_settings.cache_clear()

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()


def test_get_settings_never_returns_api_key(client_with_env_file: TestClient) -> None:
    response = client_with_env_file.get("/api/system/settings")

    assert response.status_code == 200
    assert SECRET not in response.text
    body = response.json()
    assert body["llm_has_key"] is True
    assert "llm_api_key" not in body


def test_get_settings_reports_env_path(client_with_env_file: TestClient) -> None:
    body = client_with_env_file.get("/api/system/settings").json()

    assert body["env_path"].endswith(".env")
    assert body["cors_strict"] is True, "默认应为严格模式"


def test_put_settings_writes_to_env_file(client_with_env_file: TestClient, tmp_path: Path) -> None:
    response = client_with_env_file.put(
        "/api/system/settings", json={"llm_model": "deepseek-reasoner"}
    )

    assert response.status_code == 200
    result = response.json()
    assert result["saved"] is True
    assert "LLM_MODEL" in result["changed"]

    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LLM_MODEL=deepseek-reasoner" in content
    assert "# 用户自己的注释" in content, "用户的注释必须保留"
    assert "LLM_PROVIDER=deepseek" in content


def test_put_settings_saves_api_key_without_echoing_it(
    client_with_env_file: TestClient, tmp_path: Path
) -> None:
    client_with_env_file.put("/api/system/settings", json={"llm_api_key": "sk-new-secret"})

    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-new-secret" in content  # 落盘

    later = client_with_env_file.get("/api/system/settings")
    assert "sk-new-secret" not in later.text  # 但绝不回显


def test_put_settings_ignores_non_whitelisted_fields(
    client_with_env_file: TestClient, tmp_path: Path
) -> None:
    """尝试写 app_host / session_db_path 之类的字段应被忽略。"""
    response = client_with_env_file.put(
        "/api/system/settings",
        json={"app_host": "0.0.0.0", "session_db_path": "/etc/passwd"},
    )

    assert response.status_code == 200
    assert response.json()["saved"] is False

    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "0.0.0.0" not in content
    assert "passwd" not in content


def test_put_settings_without_changes_reports_noop(client_with_env_file: TestClient) -> None:
    response = client_with_env_file.put("/api/system/settings", json={})

    assert response.json()["saved"] is False


def test_put_then_get_reflects_change_in_same_process(
    client_with_env_file: TestClient,
) -> None:
    """同一个进程内 .env 已改但内存配置未刷新 —— 接口应如实告知需重启。"""
    result = client_with_env_file.put(
        "/api/system/settings", json={"search_provider": "ddg"}
    ).json()

    assert "重启" in result["message"]
