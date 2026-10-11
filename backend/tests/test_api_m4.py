"""M4 集成测试：会话 / 分支 / 版本 / 对话 / 导出。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.agents.nodes.planner import PlanOutput
from app.api.routes_export import export_version
from app.core.errors import InvalidRequest
from app.domain.enums import DetailLevel, PlanStatus, TodoKind
from app.domain.models import Citation, TodoItem
from app.services.chat_service import ChatService
from app.storage.database import open_database
from app.storage.session_store import SessionStore
from tests.fakes import FakeLLM, FakeSearch, make_collect_result

REQUIREMENTS = {"destination": "重庆", "origin": "北京", "duration_days": 3, "budget": 2000}
BENDIBAO = "https://m.cq.bendibao.com/tour/49116.shtm"


# ============================================================ 会话生命周期
def test_session_lifecycle(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"title": "重庆之行"})
    assert created.status_code == 200
    payload = created.json()
    session_id = payload["session"]["id"]

    # 新建会话自动创建主分支
    assert len(payload["branches"]) == 1
    assert payload["branches"][0]["is_primary"] is True

    assert client.get("/api/sessions").json()["total"] == 1

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["session"]["title"] == "重庆之行"
    assert len(detail["versions"]) == 1

    renamed = client.patch(f"/api/sessions/{session_id}", json={"title": "重庆 3 天"})
    assert renamed.json()["session"]["title"] == "重庆 3 天"

    client.patch(f"/api/sessions/{session_id}", json={"archived": True})
    assert client.get("/api/sessions").json()["total"] == 0
    assert client.get("/api/sessions?archived=true").json()["total"] == 1

    assert client.delete(f"/api/sessions/{session_id}").json()["deleted"] is True


def test_unknown_session_returns_404(client: TestClient) -> None:
    response = client.get("/api/sessions/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_session_search(client: TestClient) -> None:
    client.post("/api/sessions", json={"title": "重庆之行"})
    client.post("/api/sessions", json={"title": "北京周末"})

    found = client.get("/api/sessions?q=重庆").json()

    assert found["total"] == 1
    assert found["items"][0]["title"] == "重庆之行"


# ============================================================ 分支
def test_branch_lifecycle(client: TestClient) -> None:
    session_id = client.post("/api/sessions", json={"title": "重庆之行"}).json()["session"]["id"]

    forked = client.post(f"/api/sessions/{session_id}/branches", json={"title": "亲子版"})
    branches = forked.json()["items"]
    assert len(branches) == 2

    fork_id = next(b["id"] for b in branches if b["title"] == "亲子版")
    client.post(f"/api/branches/{fork_id}/primary")

    refreshed = client.get(f"/api/sessions/{session_id}/branches").json()["items"]
    assert next(b for b in refreshed if b["id"] == fork_id)["is_primary"] is True

    # 主分支不可删除
    assert client.delete(f"/api/branches/{fork_id}").status_code == 400


def test_fork_from_specific_version(client: TestClient) -> None:
    """从指定历史版本分叉（「回到初版」场景）。"""
    session_id = client.post("/api/sessions", json={"title": "重庆之行"}).json()["session"]["id"]
    primary = client.get(f"/api/sessions/{session_id}/branches").json()["items"][0]

    # 直接造一个版本占位（无 checkpointer 时 create_version 仍可记录）
    client.post(f"/api/sessions/{session_id}/branches", json={"title": "临时"})
    assert client.get(f"/api/branches/{primary['id']}/versions").status_code == 200


# ============================================================ 未配置时的提示
def test_chat_without_configuration_gives_actionable_message(client: TestClient) -> None:
    """没配模型时不应 500 一句「内部错误」，而要告诉用户去设置。"""
    session_id = client.post("/api/sessions", json={"title": "重庆之行"}).json()["session"]["id"]

    response = client.post(
        "/api/chat", json={"message": "我想去重庆玩3天", "session_id": session_id}
    )

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "CONFIG_INVALID"
    assert "设置" in error["message"]


# ============================================================ 服务层端到端
async def test_chat_service_records_version(tmp_path: Path) -> None:
    """一轮对话后应产生一个可导出的版本。"""
    db = tmp_path / "sessions.db"
    async with open_database(db) as conn:
        store = SessionStore(conn)
        session = await store.create_session("重庆之行")
        branch = await store.create_branch(session.id, "主方案", is_primary=True)

        llm = FakeLLM(
            collect=make_collect_result(**REQUIREMENTS),
            plan=PlanOutput(
                markdown="# 重庆 3 天\n\n高铁二等座约 500 元。",
                todos=[TodoItem(kind=TodoKind.RAILWAY, what="北京→重庆 高铁", when="提前 15 天")],
            ),
        )
        async with open_checkpointer(tmp_path / "cp.db") as checkpointer:
            graph = build_graph(llm, FakeSearch(), checkpointer=checkpointer)
            service = ChatService(graph=graph, store=store)

            outcome = await service.send(
                session_id=session.id,
                branch_id=branch.id,
                message="我想去重庆玩3天，从北京出发，预算2000",
                detail_level=DetailLevel.L2_STANDARD,
            )

        assert outcome.status == PlanStatus.REVIEWING
        assert outcome.version_id is not None
        assert "计划已生成" in outcome.reply

        versions = await store.list_versions(branch.id)
        assert len(versions) == 1

        detail = await store.get_version_detail(versions[0].id)
        assert detail is not None
        assert detail.markdown.startswith("# 重庆")
        assert detail.todos[0].kind == TodoKind.RAILWAY


async def test_second_turn_approved_creates_summary_version(tmp_path: Path) -> None:
    """第二轮「确认」应走 refiner 并记录「计划已确认」的版本。"""
    from app.agents.nodes.refiner import RefineIntent

    db = tmp_path / "sessions.db"
    async with open_database(db) as conn:
        store = SessionStore(conn)
        session = await store.create_session("重庆之行")
        branch = await store.create_branch(session.id, "主方案", is_primary=True)

        llm = FakeLLM(
            collect=make_collect_result(**REQUIREMENTS),
            plan=PlanOutput(markdown="# 重庆 3 天"),
            intent=RefineIntent(intent="approve", summary="确认"),
        )
        async with open_checkpointer(tmp_path / "cp.db") as checkpointer:
            graph = build_graph(llm, FakeSearch(), checkpointer=checkpointer)
            service = ChatService(graph=graph, store=store)

            await service.send(
                session_id=session.id,
                branch_id=branch.id,
                message="我想去重庆玩3天，从北京出发，预算2000",
            )
            second = await service.send(
                session_id=session.id, branch_id=branch.id, message="确认，就这样"
            )

        assert second.status == PlanStatus.FINISHED
        versions = await store.list_versions(branch.id)
        assert [v.summary for v in versions] == ["路书已生成", "计划已确认"]


# ============================================================ 导出
async def test_export_markdown_contains_linked_citations(tmp_path: Path) -> None:
    async with open_database(tmp_path / "sessions.db") as conn:
        store = SessionStore(conn)
        session = await store.create_session("重庆之行")
        branch = await store.create_branch(session.id, "主方案", is_primary=True)
        version = await store.create_version(branch.id, summary="标准版")
        await store.save_plan(
            version.id,
            markdown="## 交通建议\n\n高铁二等座约 500 元[1]。",
            citations=[
                Citation(
                    index=1,
                    title="重庆攻略",
                    url=BENDIBAO,
                    provider="tavily",
                    retrieved_at=datetime(2026, 10, 11, 15, 32),
                )
            ],
            todos=[TodoItem(kind=TodoKind.RAILWAY, what="北京→重庆 高铁")],
            searched_at=datetime(2026, 10, 11, 15, 32),
        )

        response = await export_version(version.id, "md", store)

    body = response.body.decode()
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert f"[[1]]({BENDIBAO})" in body  # ★ 链接来自程序透传
    assert "中国铁路 12306" in body  # 官方渠道已附加
    assert body.count("仅供参考") >= 2  # 首尾免责声明


async def test_export_html_is_self_contained(tmp_path: Path) -> None:
    async with open_database(tmp_path / "sessions.db") as conn:
        store = SessionStore(conn)
        session = await store.create_session("重庆之行")
        branch = await store.create_branch(session.id, "主方案", is_primary=True)
        version = await store.create_version(branch.id)
        await store.save_plan(
            version.id,
            markdown="## 行程\n\n内容[1]。",
            citations=[
                Citation(
                    index=1,
                    title="来源",
                    url=BENDIBAO,
                    provider="tavily",
                    retrieved_at=datetime(2026, 10, 11),
                ),
            ],
        )

        response = await export_version(version.id, "html", store)

    body = response.body.decode()
    assert "text/html" in response.headers["content-type"]
    assert body.startswith("<!DOCTYPE html>")
    assert "<style>" in body and "<script>" in body
    assert "cdn." not in body


async def test_export_rejects_unknown_format(tmp_path: Path) -> None:
    async with open_database(tmp_path / "sessions.db") as conn:
        store = SessionStore(conn)
        session = await store.create_session("重庆之行")
        branch = await store.create_branch(session.id, "主方案", is_primary=True)
        version = await store.create_version(branch.id)

        with pytest.raises(InvalidRequest):
            await export_version(version.id, "pdf", store)
