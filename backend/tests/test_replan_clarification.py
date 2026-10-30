import json
from contextlib import asynccontextmanager

import httpx
import pytest
from conftest import FakeLLM, FakeSearch
from fastapi import FastAPI
from langchain_core.messages import HumanMessage
from place_fixtures import connected_place_itinerary, replan_itinerary

from app.agents.checkpoint import build_serde, open_checkpointer, thread_config
from app.agents.graph import build_graph
from app.agents.itinerary_output import PlanDetails, prepare_itinerary
from app.agents.nodes.refiner import OnTripUpdate, RefineIntent, RevisionOutput, make_refiner_node
from app.agents.state import initial_state
from app.api import routes_chat, routes_itinerary, routes_session
from app.core.config import Settings, get_settings
from app.core.errors import LLMUnavailable, register_exception_handlers
from app.domain.enums import PlanStatus
from app.domain.itinerary import DayDraft
from app.domain.models import Requirements
from app.services.chat_service import ChatService
from app.services.itinerary_service import enrich_itinerary, sync_markdown
from app.services.replan_clarification import ask_replan_day, resume_replan_day
from app.storage.database import create_connection
from app.storage.session_store import SessionStore

MESSAGE = "现在09:45我在示例酒店，18:00前结束，调整后续安排，保留预约和账目"
REQ = Requirements(origin="上海", destination="北京", duration_days=2, travelers=2)
SETTINGS = Settings(_env_file=None, amap_api_key=None)


class ReplanModel(FakeLLM):
    def __init__(self):
        self.calls = []
        self.fail_revision = False

    async def structured(self, messages, schema, **kwargs):
        self.calls.append(schema.__name__)
        if schema is RefineIntent:
            return RefineIntent(
                intent="modify" if "现在09:45" in messages[0].content else "unclear",
                on_trip=OnTripUpdate(location_name="示例酒店", time="09:45", end_time="18:00")
                if "现在09:45" in messages[0].content
                else None,
            )
        if schema is RevisionOutput:
            assert "09:45" in messages[0].content
            if self.fail_revision:
                self.fail_revision = False
                raise LLMUnavailable("测试调整模型暂不可用")
            return RevisionOutput(
                markdown="调整后的路书",
                days=[
                    DayDraft(
                        day=2, activities=[replan_itinerary().days[1].activities[1].model_dump()]
                    )
                ],
            )
        return await super().structured(messages, schema, **kwargs)


async def seed(client, store, graph):
    created = (await client.post("/api/sessions", json={"title": "旅中日期补充"})).json()
    sid, bid = created["session"]["id"], created["branches"][0]["id"]
    trip = await enrich_itinerary(replan_itinerary(), REQ, SETTINGS)
    values = {
        **initial_state(),
        "requirements": REQ,
        "itinerary": trip,
        "plan_markdown": sync_markdown("原计划", trip, REQ),
        "status": PlanStatus.REVIEWING,
    }
    config = await graph.aupdate_state(thread_config(bid), values, as_node="planner")
    await ChatService(graph=graph, store=store).record_version(
        bid, values, config, trigger="fixture"
    )
    return sid, bid


async def say(client, sid, bid, message, rid, streaming=False):
    response = await client.post(
        "/api/chat/stream" if streaming else "/api/chat",
        json={"session_id": sid, "branch_id": bid, "message": message, "request_id": rid},
    )
    if streaming:
        events = [
            dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
            for block in response.text.strip().split("\n\n")
        ]
        return events[-1]["event"], json.loads(events[-1]["data"])
    return response.status_code, response.json()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("failed", [False, True])
async def test_short_day_answer_survives_sqlite_restart_failure_and_replay(
    tmp_path, streaming, failed
):
    path, model = tmp_path / "replan.db", ReplanModel()

    @asynccontextmanager
    async def reopen():
        conn = await create_connection(path)
        try:
            async with open_checkpointer(path) as saver:
                graph = build_graph(model, FakeSearch(), settings=SETTINGS, checkpointer=saver)
                app = FastAPI()
                app.state.store, app.state.graph = SessionStore(conn), graph
                app.dependency_overrides[get_settings] = lambda: SETTINGS
                register_exception_handlers(app)
                for router in (routes_session, routes_chat, routes_itinerary):
                    app.include_router(router.router)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client, app.state.store, graph
        finally:
            await conn.close()

    async with reopen() as (client, store, graph):
        sid, bid = await seed(client, store, graph)
        original = (await store.list_versions(bid))[-1]
        before = (await client.get(f"/api/versions/{original.id}")).json()
        status, reply = await say(client, sid, bid, MESSAGE, "missing-replan-day", streaming)
        assert status == ("done" if streaming else 200)
        assert reply["clarification"]["kind"] == "replan_day"
        assert reply["clarification"]["options"] == [
            {"label": "第1天", "reply": "第1天"},
            {"label": "第2天", "reply": "第2天"},
        ]
        assert len(await store.list_versions(bid)) == 1
        pending = (await graph.aget_state(thread_config(bid))).values["pending_replan"]
        assert build_serde().loads_typed(build_serde().dumps_typed(pending)) == pending
        assert "verdict" not in json.dumps(reply["clarification"])
    if failed:
        model.fail_revision = True
        async with reopen() as (client, store, graph):
            status, _ = await say(client, sid, bid, "第2天", "resume-replan-day", streaming)
            assert status == ("error" if streaming else 502)
            assert len(await store.list_versions(bid)) == 1
            assert (await client.get(f"/api/versions/{original.id}")).json() == before
    async with reopen() as (client, store, graph):
        history = (await client.get(f"/api/branches/{bid}/messages")).json()
        assert history["clarification"]["kind"] == "replan_day"
        status, reply = await say(client, sid, bid, "第2天", "resume-replan-day", streaming)
        assert status == ("done" if streaming else 200)
        assert reply["clarification"] is None
        assert len(await store.list_versions(bid)) == 2
        after = (await client.get(f"/api/versions/{reply['version_id']}")).json()
        day = after["itinerary"]["days"][1]
        assert day["replan_context"]["time"] == "09:45"
        assert day["replan_context"]["end_time"] == "18:00"
        assert day["activities"][0]["place_name"] == "示例酒店"
        assert day["activities"][0]["start_time"] == "09:45"
        assert (
            day["activities"][1]["booking"]
            == before["itinerary"]["days"][1]["activities"][1]["booking"]
        )
        assert any("冲突" in warning for warning in day["warnings"])
        assert after["itinerary"]["days"][0] == before["itinerary"]["days"][0]
        assert after["itinerary"]["expenses"] == before["itinerary"]["expenses"]
        assert after["itinerary"]["budget_items"] == before["itinerary"]["budget_items"]
        assert model.calls.count("RefineIntent") == 1
        assert (await client.get(f"/api/versions/{original.id}")).json() == before
    async with reopen() as (client, store, graph):
        calls = list(model.calls)
        _, replay = await say(client, sid, bid, "第2天", "resume-replan-day", streaming)
        assert replay["version_id"] == reply["version_id"] and model.calls == calls
        assert len(await store.list_versions(bid)) == 2


class MissingDayModel:
    async def structured(self, messages, schema, **kwargs):
        assert schema is RefineIntent
        return RefineIntent(
            intent="modify",
            on_trip=OnTripUpdate(location_name="示例餐厅", time="09:45", end_time="18:00"),
        )


async def test_missing_replan_day_saves_original_request_without_changing_trip():
    trip = connected_place_itinerary()
    state = {
        **initial_state(),
        "requirements": Requirements(destination="北京", duration_days=2),
        "itinerary": trip,
        "plan_markdown": "原路书",
        "messages": [
            HumanMessage(content="现在09:45我在示例餐厅，18:00前结束，调整后续安排", id="request")
        ],
    }
    before = trip.model_dump()
    result = await make_refiner_node(MissingDayModel(), Settings(_env_file=None))(state)
    assert result.get("pending_replan") is not None
    assert result.get("itinerary", trip).model_dump() == before
    assert "第几天" in result["messages"][-1].content


@pytest.mark.parametrize("scope", [{"scope_day": 2}, {"scope_days": {2}}])
async def test_local_revision_keeps_expense_links_on_omitted_but_unchanged_days(scope):
    previous = await enrich_itinerary(connected_place_itinerary(), REQ, SETTINGS)
    result, _ = await prepare_itinerary(
        PlanDetails(
            days=[DayDraft(day=2, activities=[previous.days[1].activities[0].model_dump()])]
        ),
        "原计划",
        REQ,
        SETTINGS,
        0,
        previous=previous,
        **scope,
    )
    assert result.days[0] == previous.days[0]
    assert result.expenses == previous.expenses
    assert result.budget_items == previous.budget_items


async def test_actual_removal_detaches_expense_links_without_deleting_original_payment():
    previous = connected_place_itinerary()
    result, _ = await prepare_itinerary(
        PlanDetails(
            days=[DayDraft(day=1, activities=[previous.days[0].activities[2].model_dump()])]
        ),
        "原计划",
        REQ,
        SETTINGS,
        0,
        previous=previous,
        scope_day=1,
    )
    entry = result.expenses[0]
    assert entry.activity_id is entry.budget_item_id is None
    assert entry.model_dump(
        exclude={"activity_id", "budget_item_id", "settles_budget"}
    ) == previous.expenses[0].model_dump(
        exclude={"activity_id", "budget_item_id", "settles_budget"}
    )


@pytest.mark.parametrize(
    "text", ["不是第2天", "如果是第2天呢？", "第2天花了30元", "第1天和第2天", "明天"]
)
def test_unrelated_or_uncertain_answers_never_apply_pending_replan(text):
    trip = replan_itinerary()
    pending = ask_replan_day(
        MESSAGE, RefineIntent(intent="modify", on_trip=OnTripUpdate(time="09:45")), trip, "before"
    )
    assert resume_replan_day(text, pending, trip, "after") is None


async def test_invalid_day_can_be_corrected_without_reclassifying_original_intent(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    model = ReplanModel()

    async def structured(self, messages, schema, **kwargs):
        return await model.structured(messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    sid, bid = await seed(client, store, graph)
    await say(client, sid, bid, MESSAGE, "pending-original")
    _, invalid = await say(client, sid, bid, "第9天", "pending-invalid")
    assert "没有这个日期" in invalid["reply"] and invalid["clarification"]
    assert len(await store.list_versions(bid)) == 1
    _, done = await say(client, sid, bid, "是第二天", "pending-corrected")
    assert done["clarification"] is None and len(await store.list_versions(bid)) == 2
    assert model.calls.count("RefineIntent") == 1


@pytest.mark.parametrize(
    "action", ["取消这次操作", "如果是第2天呢？", "new-expense", "manual-edit", "fork"]
)
async def test_cancel_new_topic_manual_edit_and_fork_do_not_apply_stale_replan(
    environment, monkeypatch, action
):
    client, store, graph, _ = environment
    model = ReplanModel()

    async def structured(self, messages, schema, **kwargs):
        return await model.structured(messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    sid, bid = await seed(client, store, graph)
    await say(client, sid, bid, MESSAGE, "pending-before-change")
    latest = (await store.list_versions(bid))[-1]
    if action == "manual-edit":
        response = await client.post(
            f"/api/versions/{latest.id}/expenses",
            json={
                "title": "临时支出",
                "amount": "12",
                "day": 1,
                "request_id": "manual-pending-expense",
            },
        )
        assert response.status_code == 200
    elif action == "fork":
        response = await client.post(
            f"/api/sessions/{sid}/branches",
            json={"title": "新方案", "parent_branch_id": bid, "fork_version_id": latest.id},
        )
        assert response.status_code == 200
        bid = next(
            branch["id"] for branch in response.json()["items"] if branch["title"] == "新方案"
        )
    else:
        await say(
            client,
            sid,
            bid,
            "第1天午餐花了12元" if action == "new-expense" else action,
            "new-topic-pending",
        )
    history = (await client.get(f"/api/branches/{bid}/messages")).json()
    assert history["clarification"] is None
    count = len(await store.list_versions(bid))
    await say(client, sid, bid, "第2天", "after-cleared-pending")
    assert len(await store.list_versions(bid)) == count
    assert "RevisionOutput" not in model.calls


def test_changed_itinerary_rejects_old_short_answer():
    trip = replan_itinerary()
    pending = ask_replan_day(
        MESSAGE, RefineIntent(intent="modify", on_trip=OnTripUpdate(time="09:45")), trip, "before"
    )
    trip.days[1].activities[1].title = "手动改过的安排"
    result = resume_replan_day("第2天", pending, trip, "after")
    assert "行程已变化" in result.reply and result.verdict is None and result.pending is None


@pytest.mark.parametrize("answer", ["第2天", "是第二天", "选择第2天", "就是第二天！"])
def test_short_answers_only_change_day_and_keep_overnight_and_requirement_fields(answer):
    trip = replan_itinerary()
    verdict = RefineIntent(
        intent="modify",
        requirements=Requirements(budget=1800),
        clear_preferences=True,
        on_trip=OnTripUpdate(
            location_name="示例酒店", time="23:30", end_time="01:00", end_day_offset=1
        ),
    )
    original = verdict.model_dump()
    pending = ask_replan_day(
        "现在23:30在示例酒店，次日01:00前结束，调整后续安排", verdict, trip, "before"
    )
    result = resume_replan_day(answer, pending, trip, "after")
    expected = verdict.model_copy(deep=True)
    expected.on_trip.day = 2
    assert result.verdict == expected
    assert pending.verdict.model_dump() == original


@pytest.mark.parametrize("streaming", [False, True])
async def test_day_clarification_after_expense_clarification_keeps_entire_atomic_request(
    environment, monkeypatch, streaming
):
    client, store, graph, _ = environment
    model = ReplanModel()

    async def structured(self, messages, schema, **kwargs):
        return await model.structured(messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    sid, bid = await seed(client, store, graph)
    _, first = await say(client, sid, bid, "午餐花了；" + MESSAGE, "mixed-original", streaming)
    assert first["clarification"]["kind"] == "amount"
    _, second = await say(client, sid, bid, "30元", "mixed-expense-amount", streaming)
    assert second["clarification"]["kind"] == "replan_day"
    assert len(await store.list_versions(bid)) == 1
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["pending_replan"].message.endswith(
        snapshot.values["pending_replan"].refinement
    )
    _, done = await say(client, sid, bid, "第2天", "mixed-replan-day", streaming)
    assert done["clarification"] is None
    assert len(await store.list_versions(bid)) == 2
    after = (await client.get(f"/api/versions/{done['version_id']}")).json()
    assert after["itinerary"]["days"][1]["replan_context"]["time"] == "09:45"
    new = [
        entry for entry in after["itinerary"]["expenses"] if entry["id"] != "fixture-lunch-deposit"
    ]
    assert len(new) == 1 and new[0]["amount_cents"] == 3000 and new[0]["day"] is None
    assert new[0]["title"] == "午餐"
    assert model.calls.count("RefineIntent") == 1
    _, replay = await say(client, sid, bid, "第2天", "mixed-replan-day", streaming)
    assert replay["version_id"] == done["version_id"] and len(await store.list_versions(bid)) == 2
