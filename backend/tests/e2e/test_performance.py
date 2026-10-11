"""阶段③ · 性能与边界测试。

关注三件事：
1. **规模**：会话数量增长后接口是否仍然快；
2. **边界**：空输入、超长输入、超大路书是否被正确处理；
3. **并发**：多线程同时访问不会崩（HTTP 层）。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi.testclient import TestClient

from app.domain.enums import DetailLevel
from app.domain.models import Citation, TodoItem, VersionDetail, VersionInfo
from app.export.html_exporter import render_html
from app.export.markdown_exporter import render_markdown


def _new_session(client: TestClient, title: str = "性能测试") -> tuple[str, str]:
    payload = client.post("/api/sessions", json={"title": title}).json()
    return payload["session"]["id"], payload["branches"][0]["id"]


# ============================================================ 规模
def test_many_sessions_list_stays_fast(app_client: TestClient) -> None:
    """创建 100 个会话后，列表接口仍应快速返回。"""
    for i in range(100):
        app_client.post("/api/sessions", json={"title": f"旅行 {i}"})

    started = time.perf_counter()
    response = app_client.get("/api/sessions?page_size=100")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert response.json()["total"] == 100
    assert elapsed < 2.0, f"100 个会话的列表耗时 {elapsed:.2f}s，偏慢"


def test_session_search_scales(app_client: TestClient) -> None:
    for i in range(50):
        app_client.post("/api/sessions", json={"title": f"重庆 {i}" if i % 2 else f"北京 {i}"})

    found = app_client.get("/api/sessions?q=重庆").json()

    assert found["total"] == 25


# ============================================================ 边界
def test_empty_message_is_rejected(app_client: TestClient) -> None:
    session_id, branch_id = _new_session(app_client)

    response = app_client.post(
        "/api/chat", json={"message": "", "session_id": session_id, "branch_id": branch_id}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_over_long_message_is_rejected(app_client: TestClient) -> None:
    """4000 字是上限，超过应被拒绝，而不是把模型打爆。"""
    session_id, branch_id = _new_session(app_client)

    response = app_client.post(
        "/api/chat",
        json={"message": "啊" * 5000, "session_id": session_id, "branch_id": branch_id},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_long_but_valid_message_is_accepted(app_client: TestClient) -> None:
    session_id, branch_id = _new_session(app_client)

    response = app_client.post(
        "/api/chat",
        json={
            "message": "想去重庆玩3天" + "，预算2000" * 100,
            "session_id": session_id,
            "branch_id": branch_id,
        },
    )

    assert response.status_code == 200


def test_missing_session_returns_404(app_client: TestClient) -> None:
    response = app_client.post(
        "/api/chat", json={"message": "你好", "session_id": "nope", "branch_id": None}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_unknown_branch_returns_404(app_client: TestClient) -> None:
    session_id, _ = _new_session(app_client)

    assert app_client.get("/api/branches/nope/versions").status_code == 404


def test_invalid_export_format_is_rejected(app_client: TestClient) -> None:
    session_id, branch_id = _new_session(app_client)
    chat = app_client.post(
        "/api/chat",
        json={
            "message": "我想去重庆玩3天，从北京出发，预算2000",
            "session_id": session_id,
            "branch_id": branch_id,
        },
    ).json()

    response = app_client.get(f"/api/versions/{chat['version_id']}/export?format=pdf")

    assert response.status_code == 400


def test_error_responses_carry_trace_id(app_client: TestClient) -> None:
    """排障依赖 trace_id，错误路径上也必须有。"""
    body = app_client.get("/api/sessions/nope").json()

    assert body["error"]["trace_id"]


# ============================================================ 大路书导出
def _big_detail() -> VersionDetail:
    """构造一份两万字以上的路书（保姆级档位可能达到的量级）。"""
    section = "内容内容内容[1]。关于交通与住宿的说明。[1]\n\n"
    markdown = "# 超长路书\n\n" + "".join(f"## 第 {i} 节\n\n{section * 20}" for i in range(60))

    version = VersionInfo(
        id="v-big",
        branch_id="b-1",
        seq=1,
        summary="大路书",
        detail_level=DetailLevel.L4_BUTLER.value,
        created_at=datetime(2026, 10, 12),
    )
    return VersionDetail(
        version=version,
        markdown=markdown,
        citations=[
            Citation(
                index=1,
                title="来源",
                url="https://example.com/a",
                provider="tavily",
                retrieved_at=datetime(2026, 10, 12),
            )
        ],
        todos=[TodoItem(kind="railway", what="北京→重庆 高铁")],
        searched_at=datetime(2026, 10, 12),
    )


def test_large_report_renders_within_budget() -> None:
    detail = _big_detail()
    assert len(detail.markdown) > 20000

    started = time.perf_counter()
    markdown = render_markdown(detail, session_title="超长旅行")
    html = render_html(detail, session_title="超长旅行", todos=detail.todos)
    elapsed = time.perf_counter() - started

    assert len(markdown) > 20000
    assert html.startswith("<!DOCTYPE html>")
    assert "example.com" in html  # 引用链接已注入
    assert elapsed < 3.0, f"两万字路书渲染耗时 {elapsed:.2f}s"


def test_large_report_links_are_all_injected() -> None:
    """长文档中的每一个引用编号都应被替换为真实链接。"""
    html = render_html(_big_detail(), session_title="超长旅行")

    assert "https://example.com/a" in html
    assert "[1](" not in html, "不应残留未替换的 Markdown 引用标记"


# ============================================================ 并发（HTTP 层）
def test_concurrent_http_requests(app_client: TestClient) -> None:
    """多线程同时访问系统接口不应出错。"""
    paths = [
        "/api/system/health",
        "/api/system/config",
        "/api/system/providers",
    ] * 4

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(app_client.get, paths))
    elapsed = time.perf_counter() - started

    assert all(response.status_code == 200 for response in codes)
    assert elapsed < 5.0, f"12 个并发请求耗时 {elapsed:.2f}s"
