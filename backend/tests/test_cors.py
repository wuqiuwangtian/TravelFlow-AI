"""CORS 测试。

背景（真实踩过两次）：
1. 用户把前端端口从 3000 改成 3100 后，界面提示「连不上后端」——
   真实原因是 CORS 白名单没同步，而**浏览器不会告诉你这一点**；
2. 用户提出「放行任意本机端口是否存在安全问题」，因此默认收紧为白名单。

当前默认：**严格模式 + 只放行本机 3100**。
便利性由启动脚本保证：它会按实际 FRONTEND_PORT 自动注入白名单。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def _client(monkeypatch: pytest.MonkeyPatch, **env: str) -> Iterator[TestClient]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


# ============================================================ 默认：严格模式
def test_default_allows_configured_port(client: TestClient) -> None:
    """默认白名单包含本机 3100（前端默认端口）。"""
    for origin in ("http://localhost:3100", "http://127.0.0.1:3100"):
        response = client.get("/api/system/health", headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") == origin


def test_default_rejects_other_local_ports(client: TestClient) -> None:
    """★ 默认不再放行任意端口 —— 这是安全收紧的核心。"""
    response = client.get("/api/system/health", headers={"Origin": "http://localhost:4000"})

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example.com", "http://192.168.1.50:3100", "http://notlocalhost.com"],
)
def test_remote_origin_is_rejected(client: TestClient, origin: str) -> None:
    response = client.get("/api/system/health", headers={"Origin": origin})

    assert "access-control-allow-origin" not in response.headers


def test_preflight_from_allowed_port_succeeds(client: TestClient) -> None:
    """浏览器发 JSON POST 前会先发 OPTIONS 预检，这一关必须过。"""
    response = client.options(
        "/api/chat",
        headers={
            "Origin": "http://localhost:3100",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3100"


# ============================================================ 可选：宽松模式
def test_relaxed_mode_allows_any_local_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式设为 false 时才恢复「任意本机端口」的便利模式。"""
    client = next(_client(monkeypatch, APP_CORS_STRICT="false"))

    for origin in ("http://localhost:3000", "http://localhost:5199", "http://127.0.0.1:8080"):
        response = client.get("/api/system/health", headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") == origin, origin


def test_relaxed_mode_still_rejects_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """即使在宽松模式下，也不能退化成对所有来源开放。"""
    client = next(_client(monkeypatch, APP_CORS_STRICT="false"))

    response = client.get("/api/system/health", headers={"Origin": "https://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers


# ============================================================ 自定义白名单
def test_custom_whitelist_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """改了前端端口时，同步改白名单即可恢复（启动脚本会自动做这件事）。"""
    client = next(_client(monkeypatch, APP_CORS_ORIGINS="http://localhost:4200"))

    allowed = client.get("/api/system/health", headers={"Origin": "http://localhost:4200"})
    others = client.get("/api/system/health", headers={"Origin": "http://localhost:3100"})

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:4200"
    assert "access-control-allow-origin" not in others.headers


def test_empty_whitelist_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """白名单为空会让前端完全连不上，必须提前警告。"""
    client = next(_client(monkeypatch, APP_CORS_ORIGINS=""))

    warnings = client.get("/api/system/config").json()["warnings"]

    assert any("APP_CORS_ORIGINS" in w for w in warnings)


# ============================================================ 配置自检
def test_strict_mode_does_not_warn_about_cors(client: TestClient) -> None:
    warnings = client.get("/api/system/config").json()["warnings"]

    assert not any("APP_CORS_STRICT" in w for w in warnings)


def test_relaxed_mode_is_reported_as_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """宽松模式必须明示，不能悄悄生效。"""
    client = next(_client(monkeypatch, APP_CORS_STRICT="false"))

    warnings = client.get("/api/system/config").json()["warnings"]

    assert any("APP_CORS_STRICT" in w for w in warnings)


def test_settings_exposes_cors_strict_flag(client: TestClient) -> None:
    body = client.get("/api/system/settings").json()

    assert body["cors_strict"] is True


# ============================================================ 暴露到局域网
def test_warns_when_host_is_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """把服务开放到本机之外会显著放大风险，必须提醒。"""
    client = next(_client(monkeypatch, APP_HOST="0.0.0.0"))

    warnings = client.get("/api/system/config").json()["warnings"]

    assert any("本机之外" in w for w in warnings)


def test_no_host_warning_for_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    client = next(_client(monkeypatch, APP_HOST="127.0.0.1"))

    warnings = client.get("/api/system/config").json()["warnings"]

    assert not any("本机之外" in w for w in warnings)
