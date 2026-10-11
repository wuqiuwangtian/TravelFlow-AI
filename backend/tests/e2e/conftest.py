"""阶段③ 端到端测试的公共夹具。

放在 conftest.py 里由 pytest 自动发现 ——
若从别的测试模块 import fixture，ruff 会报 F811（重定义）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.graph import build_graph
from app.agents.nodes.planner import PlanOutput
from app.agents.nodes.refiner import RefineIntent
from app.core.config import get_settings
from app.domain.models import TodoItem
from app.main import create_app
from tests.fakes import FakeLLM, FakeSearch, make_collect_result


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """启动真实应用，但把「大脑」换成假 LLM + 假检索。

    这样既走完整的 lifespan / 路由 / 存储 / 导出链路，
    又不消耗任何真实 API 额度。
    """
    monkeypatch.setenv("SESSION_DB_PATH", str(tmp_path / "e2e.db"))
    monkeypatch.setenv("APP_CORS_ORIGINS", "http://localhost:3100")
    # 给一个假 Key，让 lifespan 正常构建「图 + checkpointer」，
    # 随后立即替换为假实现 —— 既不碰真实网络，又能复用持久化能力。
    monkeypatch.setenv("LLM_API_KEY", "sk-fake-for-e2e")
    monkeypatch.setenv("SEARCH_API_KEY", "tvly-fake-for-e2e")
    get_settings.cache_clear()

    llm = FakeLLM(
        collect=make_collect_result(
            destination="重庆", origin="北京", duration_days=3, budget=2000
        ),
        plan=PlanOutput(
            markdown="# 重庆 3 天 2 夜\n\n## 交通建议\n\n高铁二等座约 500 元[1]。",
            todos=[TodoItem(kind="railway", what="北京→重庆 高铁二等座", when="提前 15 天")],
        ),
        intent=RefineIntent(intent="approve", summary="用户确认"),
        refine_text="# 重庆 3 天 2 夜（已调整）\n\n## 交通建议\n\n改为轻松版。",
    )
    search = FakeSearch()

    with TestClient(create_app()) as client:
        assert client.app.state.checkpointer is not None
        client.app.state.graph = build_graph(
            llm, search, checkpointer=client.app.state.checkpointer
        )
        client.app.state.fake_llm = llm
        yield client

    get_settings.cache_clear()


@pytest.fixture
def new_session(app_client: TestClient):
    """便捷方法：新建会话并返回 (session_id, branch_id)。"""

    def _create(title: str = "测试旅行") -> tuple[str, str]:
        payload = app_client.post("/api/sessions", json={"title": title}).json()
        return payload["session"]["id"], payload["branches"][0]["id"]

    return _create
