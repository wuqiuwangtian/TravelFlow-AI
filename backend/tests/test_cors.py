"""CORS 回归测试。

背景：用户把前端端口从 3000 改成 3100 后，界面提示「连不上后端」。
真实原因却是 CORS 白名单没同步 —— 浏览器不会告诉你这一点，排查成本很高。

因此这里把「任意本机来源都放行」固化为测试：
- 本机任意端口（3000 / 3100 / 5173 …）必须通过；
- 非本机来源必须被拒绝（不能退化成 allow_origins=["*"]）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

LOCAL_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3100",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5173",
    "https://localhost:4443",
]

REMOTE_ORIGINS = [
    "https://evil.example.com",
    "http://192.168.1.50:3100",
    "http://notlocalhost.com",
]


@pytest.mark.parametrize("origin", LOCAL_ORIGINS)
def test_local_origin_any_port_is_allowed(client: TestClient, origin: str) -> None:
    response = client.get("/api/system/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("origin", REMOTE_ORIGINS)
def test_non_local_origin_is_rejected(client: TestClient, origin: str) -> None:
    """不能因为图方便就退化成对所有来源开放。"""
    response = client.get("/api/system/health", headers={"Origin": origin})

    assert "access-control-allow-origin" not in response.headers


def test_preflight_from_local_port_succeeds(client: TestClient) -> None:
    """浏览器在发 JSON POST 前会先发 OPTIONS 预检，这一关也必须过。"""
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


def test_explicit_origins_still_honoured(client: TestClient) -> None:
    """APP_CORS_ORIGINS 追加的来源依然有效（正则之外的补充）。"""
    response = client.get("/api/system/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
