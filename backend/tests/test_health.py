"""健康检查与根路径测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/system/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["uptime_seconds"] >= 0


def test_health_does_not_depend_on_external_services(client: TestClient) -> None:
    """健康检查必须永远能答，即使没有配置任何密钥。"""
    for _ in range(3):
        assert client.get("/api/system/health").status_code == 200


def test_root_gives_entry_hints(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["health"] == "/api/system/health"
    assert body["docs"] == "/docs"
