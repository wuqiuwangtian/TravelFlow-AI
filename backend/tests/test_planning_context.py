import json
from datetime import date

from langchain_core.messages import AIMessage

from app.agents.checkpoint import thread_config
from app.domain.enums import PlanStatus
from app.domain.models import Requirements
from app.services.planning_context import planning_context


def test_unknown_values_are_not_replaced_by_defaults_or_message_guesses():
    result = planning_context({"messages": [AIMessage(content="两个人预算3000元")]}).model_dump()
    assert result["status"] is None
    assert result["requirements"]["travelers"] is None
    assert result["requirements"]["budget"] is None
    assert result["requirements"]["budget_basis"] is None
    assert result["requirements"]["departure_date"] is None
    assert result["missing_fields"] == list(Requirements.REQUIRED_FIELDS)


def test_zero_budget_and_optional_unknowns_keep_their_meaning():
    context = planning_context(
        {
            "requirements": Requirements(
                destination="成都", origin="上海", duration_days=2, budget=0
            ),
            "missing_fields": ["budget"],  # 旧过程中的列表不应覆盖真实字段。
            "status": PlanStatus.COLLECTING,
        }
    )
    assert context.missing_fields == []
    assert context.requirements.budget == 0
    assert context.requirements.travelers is None
    assert context.requirements.budget_basis is None


async def test_history_projects_checkpoint_without_mutations_or_private_state(environment):
    client, store, graph, search = environment
    trip = (await client.post("/api/sessions", json={})).json()
    bid = trip["branches"][0]["id"]
    config = thread_config(bid)
    req = Requirements(
        origin="上海", duration_days=3, budget=2500, departure_date=date(2026, 11, 12)
    )
    await graph.aupdate_state(
        config,
        {
            "requirements": req,
            "status": PlanStatus.COLLECTING,
            "destination_ideas": ["成都", "重庆"],
            "destination_recommendation_requested": True,
            "chat_request": {"id": "private-receipt"},
            "trace_id": "private-trace",
        },
    )
    before = await graph.aget_state(config)
    for _ in range(2):
        response = await client.get(f"/api/branches/{bid}/messages")
        assert response.status_code == 200
        context = response.json()["planning_context"]
        assert context["requirements"]["origin"] == "上海"
        assert context["requirements"]["departure_date"] == "2026-11-12"
        assert context["missing_fields"] == ["destination"]
        assert context["destination_ideas"] == ["成都", "重庆"]
        assert context["destination_recommendation_requested"] is True
        assert "private-" not in response.text
    assert (await graph.aget_state(config)).config == before.config
    assert await store.list_versions(bid) == []
    assert search.queries == []


async def test_empty_branch_summary_does_not_inherit_other_branch_conditions(environment):
    client, store, graph, _ = environment
    trip = (await client.post("/api/sessions", json={})).json()
    sid, bid = trip["session"]["id"], trip["branches"][0]["id"]
    await graph.aupdate_state(thread_config(bid), {"requirements": Requirements(origin="上海")})
    other = await store.create_branch(sid, "独立方案")
    first = (await client.get(f"/api/branches/{bid}/messages")).json()["planning_context"]
    second = (await client.get(f"/api/branches/{other.id}/messages")).json()["planning_context"]
    assert first["requirements"]["origin"] == "上海"
    assert second["requirements"]["origin"] is None
    assert second["missing_fields"] == list(Requirements.REQUIRED_FIELDS)


async def test_chat_stream_history_and_replay_share_the_summary(environment):
    client, store, _, _ = environment
    trip = (await client.post("/api/sessions", json={})).json()
    sid, bid = trip["session"]["id"], trip["branches"][0]["id"]
    payload = {"session_id": sid, "message": "北京三日游", "request_id": "context-stream-request"}
    streamed = await client.post("/api/chat/stream", json=payload)
    done = next(event for event in streamed.text.split("\n\n") if event.startswith("event: done"))
    context = json.loads(done.split("data: ", 1)[1])["planning_context"]
    assert context["requirements"]["destination"] == "北京"
    assert context["status"] == "reviewing"
    assert context["missing_fields"] == []
    assert (await client.get(f"/api/branches/{bid}/messages")).json()["planning_context"] == context
    assert (await client.post("/api/chat", json=payload)).json()["planning_context"] == context
    assert len(await store.list_versions(bid)) == 1


async def test_legacy_receipt_without_summary_can_still_replay(environment, monkeypatch):
    client, store, _, _ = environment
    original = store.save_chat_receipt

    async def save_legacy(branch_id, request_id, fingerprint, outcome):
        legacy = {key: value for key, value in outcome.items() if key != "planning_context"}
        await original(branch_id, request_id, fingerprint, legacy)

    monkeypatch.setattr(store, "save_chat_receipt", save_legacy)
    trip = (await client.post("/api/sessions", json={})).json()
    sid, bid = trip["session"]["id"], trip["branches"][0]["id"]
    payload = {"session_id": sid, "message": "北京三日游", "request_id": "legacy-context-receipt"}
    first = (await client.post("/api/chat", json=payload)).json()
    replay = (await client.post("/api/chat", json=payload)).json()
    assert replay["version_id"] == first["version_id"]
    assert replay["planning_context"] is None
    assert len(await store.list_versions(bid)) == 1
    restored = (await client.get(f"/api/branches/{bid}/messages")).json()["planning_context"]
    assert restored["requirements"]["destination"] == "北京"
