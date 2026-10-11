"""统一错误格式测试 —— 任何异常都必须返回同一套结构。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_trace_id_returned_in_response_header(client: TestClient) -> None:
    response = client.get("/api/system/health")

    assert response.headers.get("X-Trace-Id")


def test_unknown_path_uses_unified_error_format(client: TestClient) -> None:
    response = client.get("/api/definitely-not-here")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["message"]  # 面向用户，必须非空
    assert error["trace_id"]


def test_business_error_maps_to_declared_code_and_status() -> None:
    from app.core.errors import SearchUnavailable

    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise SearchUnavailable()

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SEARCH_PROVIDER_UNAVAILABLE"


def test_unhandled_exception_does_not_leak_internals() -> None:
    """技术细节只进日志，绝不进响应体。"""
    app = create_app()

    @app.get("/crash")
    async def crash() -> None:
        raise ValueError("内部数据库密码是 123456")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/crash")

    assert response.status_code == 500
    assert "123456" not in response.text
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
