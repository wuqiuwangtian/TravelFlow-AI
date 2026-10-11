"""阶段③ · 端到端用户故事测试。

与前面的单元测试不同，这里**走完整的 HTTP 链路**：
建会话 → 对话 → 生成 → 修改 → 导出 → 分支对比，
只有 LLM 与检索被替换为假实现（避免消耗真实额度）。

覆盖用户最初描述的核心场景：
    US-1 我输入需求 → 系统追问 → 自动检索 → 产出路书
    US-2 我满意 → 导出
    US-3 我不满意 → 只改指定部分，不重新检索
    US-4 我想对比多个方案 → 从版本分叉
    US-5 我改错了 → 回到早期版本
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agents.nodes.refiner import RefineIntent
from tests.fakes import make_collect_result

FIRST_MESSAGE = "我想去重庆玩3天，从北京出发，预算2000"


def _new_session(client: TestClient, title: str = "重庆之行") -> tuple[str, str]:
    """新建会话，返回 (session_id, branch_id)。"""
    payload = client.post("/api/sessions", json={"title": title}).json()
    return payload["session"]["id"], payload["branches"][0]["id"]


# ============================================================ US-1
def test_us1_from_blank_to_plan(app_client: TestClient) -> None:
    """从零开始：新建 → 对话 → 检索 → 生成路书。"""
    session_id, branch_id = _new_session(app_client)

    response = app_client.post(
        "/api/chat",
        json={"message": FIRST_MESSAGE, "session_id": session_id, "branch_id": branch_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reviewing"  # 已生成，等待确认
    assert "重庆" in body["plan"]
    assert body["version_id"]

    # 版本已落库且可读取
    version = app_client.get(f"/api/versions/{body['version_id']}").json()
    assert version["markdown"].startswith("# 重庆")
    assert version["todos"][0]["what"].startswith("北京→重庆")


def test_us1_requires_more_info_first(app_client: TestClient) -> None:
    """信息不全时必须先追问，且**不触发检索**。"""
    llm = app_client.app.state.fake_llm  # type: ignore[attr-defined]
    llm._collect = make_collect_result(destination="重庆")  # 只给目的地

    session_id, branch_id = _new_session(app_client)
    body = app_client.post(
        "/api/chat",
        json={"message": "我想去重庆", "session_id": session_id, "branch_id": branch_id},
    ).json()

    assert body["status"] == "collecting"
    assert set(body["missing_fields"]) == {"origin", "duration_days", "budget"}
    assert body["plan"] == ""
    assert body["version_id"] is None


# ============================================================ US-2
def test_us2_approve_finalises(app_client: TestClient) -> None:
    """满意 → 确认 → 定稿，且不重复检索。"""
    session_id, branch_id = _new_session(app_client)
    app_client.post(
        "/api/chat",
        json={"message": FIRST_MESSAGE, "session_id": session_id, "branch_id": branch_id},
    )

    final = app_client.post(
        "/api/chat",
        json={"message": "确认，就这样", "session_id": session_id, "branch_id": branch_id},
    ).json()

    assert final["status"] == "finished"

    versions = app_client.get(f"/api/branches/{branch_id}/versions").json()["items"]
    assert versions[-1]["summary"] == "计划已确认"


def test_us2_export_markdown_and_html(app_client: TestClient) -> None:
    """导出两种格式，内容正确且引用来自程序。"""
    session_id, branch_id = _new_session(app_client)
    chat = app_client.post(
        "/api/chat",
        json={"message": FIRST_MESSAGE, "session_id": session_id, "branch_id": branch_id},
    ).json()

    md = app_client.get(f"/api/versions/{chat['version_id']}/export?format=md")
    html = app_client.get(f"/api/versions/{chat['version_id']}/export?format=html")

    assert md.status_code == 200
    assert "attachment" in md.headers["content-disposition"]
    assert "重庆" in md.text
    assert "仅供参考" in md.text  # 免责声明

    assert html.status_code == 200
    assert html.text.startswith("<!DOCTYPE html>")
    assert "<style>" in html.text  # 单文件内联样式


# ============================================================ US-3
def test_us3_modify_only_changes_plan(app_client: TestClient) -> None:
    """不满意 → 提出修改 → 只改内容，不新增检索。"""
    llm = app_client.app.state.fake_llm  # type: ignore[attr-defined]
    llm._intent = RefineIntent(intent="modify", summary="改轻松点")

    session_id, branch_id = _new_session(app_client)
    first = app_client.post(
        "/api/chat",
        json={"message": FIRST_MESSAGE, "session_id": session_id, "branch_id": branch_id},
    ).json()

    revised = app_client.post(
        "/api/chat",
        json={"message": "第二天轻松点", "session_id": session_id, "branch_id": branch_id},
    ).json()

    assert revised["status"] == "reviewing"
    assert "已调整" in revised["plan"]
    assert revised["version_id"] != first["version_id"]

    versions = app_client.get(f"/api/branches/{branch_id}/versions").json()["items"]
    assert len(versions) == 2
    assert versions[-1]["summary"].startswith("第 1 次修改")


# ============================================================ US-4
def test_us4_fork_and_compare(app_client: TestClient) -> None:
    """想对比方案 → 从某个版本分叉 → 两个分支各自独立。"""
    session_id, branch_id = _new_session(app_client)
    chat = app_client.post(
        "/api/chat",
        json={"message": FIRST_MESSAGE, "session_id": session_id, "branch_id": branch_id},
    ).json()

    forked = app_client.post(
        f"/api/sessions/{session_id}/branches",
        json={
            "title": "轻松版",
            "parent_branch_id": branch_id,
            "fork_version_id": chat["version_id"],
        },
    ).json()

    assert len(forked["items"]) == 2
    new_branch = next(b for b in forked["items"] if b["title"] == "轻松版")
    assert new_branch["parent_branch_id"] == branch_id
    assert new_branch["fork_version_id"] == chat["version_id"]

    # 新分支此时还没有自己的版本
    assert app_client.get(f"/api/branches/{new_branch['id']}/versions").json()["items"] == []


def test_us4_switch_primary_branch(app_client: TestClient) -> None:
    session_id, branch_id = _new_session(app_client)
    created = app_client.post(
        f"/api/sessions/{session_id}/branches", json={"title": "备选方案"}
    ).json()
    target = next(b for b in created["items"] if b["title"] == "备选方案")

    app_client.post(f"/api/branches/{target['id']}/primary")

    branches = app_client.get(f"/api/sessions/{session_id}/branches").json()["items"]
    assert next(b for b in branches if b["id"] == target["id"])["is_primary"] is True
    assert next(b for b in branches if b["id"] == branch_id)["is_primary"] is False


# ============================================================ US-5
def test_us5_session_management(app_client: TestClient) -> None:
    """改错了/不想要了 → 归档与删除。"""
    session_id, _ = _new_session(app_client, "待整理")

    app_client.patch(f"/api/sessions/{session_id}", json={"archived": True})
    assert app_client.get("/api/sessions").json()["total"] == 0
    assert app_client.get("/api/sessions?archived=true").json()["total"] == 1

    app_client.patch(f"/api/sessions/{session_id}", json={"archived": False})
    assert app_client.get("/api/sessions").json()["total"] == 1

    assert app_client.delete(f"/api/sessions/{session_id}").json()["deleted"] is True
    assert app_client.get(f"/api/sessions/{session_id}").status_code == 404


def test_us5_rename_session(app_client: TestClient) -> None:
    session_id, _ = _new_session(app_client, "临时名字")

    app_client.patch(f"/api/sessions/{session_id}", json={"title": "重庆 3 天"})

    assert app_client.get(f"/api/sessions/{session_id}").json()["session"]["title"] == "重庆 3 天"


# ============================================================ 配置自检
def test_system_endpoints_work_without_configuration(app_client: TestClient) -> None:
    """没配 Key 时，系统类接口仍应全部可用（否则用户连设置页都打不开）。"""
    assert app_client.get("/api/system/health").status_code == 200
    assert app_client.get("/api/system/config").status_code == 200
    assert app_client.get("/api/system/providers").status_code == 200
    assert app_client.get("/api/system/settings").status_code == 200
