import json

import pytest
from conftest import FakeLLM
from place_fixtures import candidates, connected_place_itinerary, place_itinerary

from app.agents.checkpoint import build_serde, thread_config
from app.agents.state import initial_state
from app.core.config import Settings
from app.core.errors import ProviderError
from app.domain.enums import PlanStatus
from app.domain.itinerary import Booking, BudgetItem, Place, ReplanContext, RouteLeg
from app.domain.models import Requirements
from app.maps.amap import AmapProvider
from app.services.chat_service import ChatService
from app.services.itinerary_service import sync_markdown
from app.services.place_messages import handle_place_message

REQ = Requirements(
    destination="北京", origin="上海", duration_days=2, budget=2000, budget_basis="total"
)
SETTINGS = Settings(_env_file=None, amap_api_key=None)


@pytest.mark.parametrize("route_state", ["unconfigured", "failed", "fresh"])
@pytest.mark.parametrize("matched", [True, False])
async def test_changing_place_rechecks_both_adjacent_routes(monkeypatch, route_state, matched):
    async def route(self, origin, destination, mode):
        if route_state == "failed":
            raise ProviderError("测试路线暂不可用")
        return RouteLeg(
            from_id="",
            to_id="",
            mode=mode,
            status="ok",
            duration_seconds=7200 if origin.id == "west" else 600,
            cost=4,
            cost_basis="total",
            cost_source="reference",
        )

    monkeypatch.setattr(AmapProvider, "route", route)
    settings = Settings(
        _env_file=None, amap_api_key=None if route_state == "unconfigured" else "test-map-only"
    )
    plan = connected_place_itinerary(matched=matched)
    before = plan.model_dump()
    result = await handle_place_message("第1天第2站的地址在西城区那家", plan, REQ, settings)
    day = result.itinerary.days[0]
    assert day.activities[1].place.id == "west"
    for target in day.activities[1:3]:
        assert target.transport_minutes is target.transport_cost is None
        assert target.transport_cost_basis == "unknown" and target.transport_source_indices == []
    if route_state == "fresh":
        assert [leg.duration_seconds for leg in day.legs[:2]] == [600, 7200]
        assert [leg.cost for leg in day.legs[:2]] == [4, 4]
        assert any("入住酒店" in warning and "冲突" in warning for warning in day.warnings)
        assert "仍有时间冲突" in result.reply
    else:
        assert all(leg.duration_seconds is leg.cost is None for leg in day.legs[:2])
    assert day.activities[2].booking == plan.days[0].activities[2].booking
    assert day.activities[2].start_time == "12:00"
    assert day.activities[3].transport_minutes == 15 and day.activities[3].transport_cost == 9
    assert result.itinerary.expenses == plan.expenses
    assert result.itinerary.budget_items == plan.budget_items
    assert result.itinerary.days[1] == plan.days[1]
    assert plan.model_dump() == before


async def test_confirming_same_place_keeps_existing_route_estimates():
    plan = connected_place_itinerary()
    result = await handle_place_message("第1天第2站的地址在朝阳区那家", plan, REQ, SETTINGS)
    day = result.itinerary.days[0]
    assert day.activities[1].place.id == "east"
    assert [leg.duration_seconds for leg in day.legs[:2]] == [300, 600]
    assert [leg.cost for leg in day.legs[:2]] == [6, 8]


async def test_place_change_keeps_explicit_manual_transport_budget_with_unknown_duration():
    plan = connected_place_itinerary()
    plan.budget_items.append(
        BudgetItem(
            id="manual-next-leg",
            title="人工接驳预估",
            category="transport",
            day=1,
            activity_id="hotel",
            source="transport",
            planned_cents=1234,
        )
    )
    result = await handle_place_message("第1天第2站的地址在西城区那家", plan, REQ, SETTINGS)
    leg = result.itinerary.days[0].legs[1]
    assert leg.duration_seconds is None
    assert leg.cost == 12.34 and leg.cost_source == "manual"
    assert result.itinerary.expenses == plan.expenses
    assert result.itinerary.budget_items == plan.budget_items


async def seed(environment, itinerary=None):
    client, store, graph, _ = environment
    session = (await client.post("/api/sessions", json={"title": "地点澄清"})).json()
    sid, bid = session["session"]["id"], session["branches"][0]["id"]
    itinerary = itinerary or place_itinerary()
    markdown = sync_markdown("测试路书", itinerary, REQ)
    values = {
        **initial_state(),
        "requirements": REQ,
        "itinerary": itinerary,
        "plan_markdown": markdown,
        "status": PlanStatus.REVIEWING,
    }
    config = await graph.aupdate_state(thread_config(bid), values, as_node="planner")
    await ChatService(graph=graph, store=store).record_version(
        bid, values, config, trigger="fixture"
    )

    async def say(text, request_id=None, stream=False):
        response = await client.post(
            "/api/chat/stream" if stream else "/api/chat",
            json={"session_id": sid, "branch_id": bid, "message": text, "request_id": request_id},
        )
        if stream:
            return json.loads(response.text.split("event: done\ndata: ")[1].split("\n\n")[0])
        assert response.status_code == 200, response.text
        return response.json()

    return sid, bid, say


@pytest.mark.parametrize("entry", ["edit", "chat", "stream"])
async def test_place_change_persists_unknown_routes_and_exports_without_extra_versions(
    environment, monkeypatch, entry
):
    client, store, _, _ = environment
    _, bid, say = await seed(environment, connected_place_itinerary(matched=False))
    original = (await store.list_versions(bid))[-1]
    before = (await client.get(f"/api/versions/{original.id}")).json()

    async def no_model(*args, **kwargs):
        raise AssertionError("地点确认和编辑不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", no_model)
    if entry == "edit":
        old = before["itinerary"]["days"][0]["activities"][1]
        edited = await client.put(
            f"/api/versions/{original.id}/days/1/activities/{old['id']}",
            json={"activity": {**old, "place_name": "示例餐厅(西单店)"}},
        )
        assert edited.status_code == 200, edited.text
    else:
        question = await say("确认第1天第2站的地点", stream=entry == "stream")
        assert question["clarification"]["kind"] == "place"
        assert len(await store.list_versions(bid)) == 1
        confirmed = await say("选第二个", "new-place-routes-once", stream=entry == "stream")
        replay = await say("选第二个", "new-place-routes-once", stream=entry == "stream")
        assert replay["version_id"] == confirmed["version_id"]
    versions = await store.list_versions(bid)
    assert len(versions) == 2
    detail = (await client.get(f"/api/versions/{versions[-1].id}")).json()
    day = detail["itinerary"]["days"][0]
    assert "西单店" in day["activities"][1]["place_name"]
    for leg in day["legs"][:2]:
        assert leg["duration_seconds"] is leg["cost"] is None
    assert (
        day["activities"][2]["booking"]
        == before["itinerary"]["days"][0]["activities"][2]["booking"]
    )
    assert detail["itinerary"]["expenses"] == before["itinerary"]["expenses"]
    assert detail["itinerary"]["budget_items"] == before["itinerary"]["budget_items"]
    assert detail["itinerary"]["days"][1] == before["itinerary"]["days"][1]
    for fmt in ("md", "html"):
        exported = await client.get(f"/api/versions/{versions[-1].id}/export?format={fmt}")
        assert exported.status_code == 200
        assert "西单店" in exported.text and "12:00" in exported.text and "待核实" in exported.text
    assert (await client.get(f"/api/versions/{original.id}")).json() == before
    assert (await client.get(f"/api/versions/{versions[-1].id}")).json() == detail
    assert len(await store.list_versions(bid)) == 2


async def test_edit_at_same_poi_keeps_route_quotes_when_only_name_is_canonicalised(environment):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment, connected_place_itinerary())
    original = (await store.list_versions(bid))[-1]
    before = (await client.get(f"/api/versions/{original.id}")).json()
    old = before["itinerary"]["days"][0]["activities"][1]
    edited = await client.put(
        f"/api/versions/{original.id}/days/1/activities/{old['id']}",
        json={"activity": {**old, "title": "午餐说明补充"}, "place_id": "east"},
    )
    assert edited.status_code == 200, edited.text
    day = edited.json()["itinerary"]["days"][0]
    assert day["activities"][1]["place_name"] == "示例餐厅(望京店)"
    assert [leg["duration_seconds"] for leg in day["legs"][:2]] == [300, 600]
    assert [leg["cost"] for leg in day["legs"][:2]] == [6, 8]


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("选第二个", "west"),
        ("朝阳区那家", "east"),
        ("地址是西城区西单路2号", "west"),
        ("示例餐厅(望京店)", "east"),
        ("北京市朝阳区那家", "east"),
        ("北京朝阳区那家", "east"),
        ("地址是朝阳区望京路1号", "east"),
    ],
)
async def test_candidate_number_region_address_and_name_apply_without_model(
    environment, monkeypatch, answer, expected
):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)

    async def offline(*args, **kwargs):
        raise AssertionError("明确地点选择不能交给模型编坐标")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    result = await say("确认第1天第1站的地点", stream=True)
    assert result["clarification"]["kind"] == "place"
    assert len(result["clarification"]["options"]) == 2
    assert len(await store.list_versions(bid)) == 1
    history = (await client.get(f"/api/branches/{bid}/messages")).json()
    assert history["clarification"] == result["clarification"]
    snapshot = await graph.aget_state(thread_config(bid))
    pending = snapshot.values["pending_place"]
    assert build_serde().loads_typed(build_serde().dumps_typed(pending)) == pending
    result = await say(answer, request_id="confirm-place-once", stream=True)
    assert result["clarification"] is None
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.days[0].activities[0].place.id == expected
    assert current.days[1] == before.days[1]
    assert current.expenses == before.expenses and current.budget_items == before.budget_items
    assert current.days[0].activities[0].cost == 88
    assert current.days[0].legs[0].from_id == "lunch"
    assert len(await store.list_versions(bid)) == 2
    assert (await say(answer, request_id="confirm-place-once"))["version_id"] == result[
        "version_id"
    ]
    for fmt in ("md", "html"):
        exported = await client.get(f"/api/versions/{result['version_id']}/export?format={fmt}")
        assert next(p.name for p in candidates() if p.id == expected).split("(")[0] in exported.text
        assert "88.00" in exported.text and "20.00" in exported.text
        assert next(p.address for p in candidates() if p.id == expected) in exported.text


async def test_unknown_target_can_be_selected_then_place_chosen(environment):
    _, store, graph, _ = environment
    _, bid, say = await seed(environment)
    result = await say("确认待定位的地点")
    assert len(result["clarification"]["options"]) == 2
    result = await say("第2天第1站")
    assert "晚餐" in result["clarification"]["prompt"]
    await say("选第1个")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.days[1].activities[0].place.id == "east"
    assert current.days[0].activities[0].place is None
    assert len(await store.list_versions(bid)) == 2


async def test_ambiguous_area_invalid_number_and_cancel_do_not_change_plan(environment):
    _, store, graph, _ = environment
    plan = place_itinerary()
    plan.days[0].activities[0].candidates[1].district = "朝阳区"
    _, bid, say = await seed(environment, plan)
    await say("确认第1天第1站的地点")
    assert (await say("选第8个"))["clarification"]
    assert "多个候选" in (await say("朝阳区那家"))["reply"]
    result = await say("取消这次操作")
    assert result["clarification"] is None
    assert len(await store.list_versions(bid)) == 1
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].days[0].activities[
        0
    ].place is None


@pytest.mark.parametrize("status", ["completed", "in_progress", "skipped", "booked"])
async def test_history_and_bookings_are_protected(environment, status):
    _, store, graph, _ = environment
    plan = place_itinerary()
    if status == "booked":
        plan.days[0].activities[0].booking = Booking(start_time="12:00", duration_minutes=60)
    else:
        plan.days[0].activities[0].progress = status
    _, bid, say = await seed(environment, plan)
    result = await say("确认第1天第1站的地点")
    assert "已有预约或旅中记录" in result["reply"]
    assert len(await store.list_versions(bid)) == 1
    assert (await graph.aget_state(thread_config(bid))).values["pending_place"] is None


async def test_explicit_region_in_initial_message_is_applied_directly(environment):
    _, _, graph, _ = environment
    _, bid, say = await seed(environment)
    result = await say("第1天第1站的地点在朝阳区那家")
    assert result["clarification"] is None
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].days[0].activities[
        0
    ].place.id == "east"


async def test_fresh_search_replaces_candidates_and_route_budget_is_recomputed(monkeypatch):
    new = Place(
        id="new",
        name="新餐厅(东街店)",
        city="北京",
        district="东城区",
        address="东街8号",
        longitude=116.41,
        latitude=39.92,
        typecode="050100",
    )

    async def search(self, keyword, city):
        assert "新餐厅" in keyword
        return [new]

    async def route(self, origin, destination, mode):
        return RouteLeg(
            from_id="",
            to_id="",
            mode=mode,
            status="ok",
            duration_seconds=600,
            walking_meters=50,
            cost=5,
            cost_basis="total",
            cost_source="reference",
        )

    monkeypatch.setattr(AmapProvider, "search_places", search)
    monkeypatch.setattr(AmapProvider, "route", route)
    plan = place_itinerary()
    result = await handle_place_message(
        "把第1天第1站换成新餐厅", plan, REQ, Settings(_env_file=None, amap_api_key="test-map-only")
    )
    assert result.itinerary is not None
    assert result.itinerary.days[0].activities[0].place.id == "new"
    assert result.itinerary.days[0].legs[0].duration_seconds == 600
    assert result.itinerary.days[0].known_cost == 393
    assert result.itinerary.expenses == plan.expenses and result.itinerary.days[1] == plan.days[1]


@pytest.mark.parametrize(
    "text",
    [
        "第二个多少钱？",
        "不要选第二个",
        "第二天轻松一点",
        "午餐花了86元",
        "如果选朝阳区那家",
        "明天想去公园",
    ],
)
async def test_questions_negative_statements_and_other_topics_do_not_select(text):
    plan = place_itinerary()
    initial = await handle_place_message("确认第1天第1站的地点", plan, REQ, SETTINGS)
    assert await handle_place_message(text, plan, REQ, SETTINGS, initial.pending) is None
    assert plan.days[0].activities[0].place is None


async def test_map_failure_can_retry_original_answer_and_does_not_save_partial_change(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    await say("确认第1天第1站的地点")

    async def unavailable(*args):
        raise ProviderError("测试地图故障")

    monkeypatch.setattr("app.services.place_messages._search", unavailable)
    sid = (await store.get_branch(bid)).session_id
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "message": "地址是海淀区中关村路5号",
        "request_id": "retry-place-search",
    }
    assert (await client.post("/api/chat", json=payload)).status_code == 502
    assert len(await store.list_versions(bid)) == 1
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].days[0].activities[
        0
    ].place is None

    async def found(*args):
        return [
            Place(
                id="new-found",
                name="示例餐厅(中关村店)",
                city="北京",
                district="海淀区",
                address="中关村路5号",
                longitude=116.3,
                latitude=39.98,
            )
        ]

    monkeypatch.setattr("app.services.place_messages._search", found)
    response = await client.post("/api/chat", json=payload)
    assert response.status_code == 200
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].days[0].activities[
        0
    ].place.id == "new-found"
    assert len(await store.list_versions(bid)) == 2


async def test_replacement_request_keeps_the_new_place_while_asking_for_target(monkeypatch):
    seen = []
    new = candidates()[0].model_copy(update={"id": "new", "name": "新餐厅"})

    async def search(pending, hint, settings):
        seen.append(pending.query_name)
        return [new]

    monkeypatch.setattr("app.services.place_messages._search", search)
    plan = place_itinerary()
    requested = await handle_place_message("把示例餐厅换成新餐厅", plan, REQ, SETTINGS)
    assert requested.pending.activity_id is None and requested.pending.replacement
    result = await handle_place_message("第2天第1站", plan, REQ, SETTINGS, requested.pending)
    assert seen == ["新餐厅"]
    assert result.itinerary.days[1].activities[0].place.id == "new"
    assert result.itinerary.days[0] == plan.days[0]


async def test_a_single_search_result_must_match_the_supplementary_address(monkeypatch):
    async def wrong(*args):
        return [candidates()[0]]

    monkeypatch.setattr("app.services.place_messages._search", wrong)
    plan = place_itinerary()
    initial = await handle_place_message("确认第1天第1站的地点", plan, REQ, SETTINGS)
    reply = await handle_place_message(
        "地址是海淀区中关村路5号", plan, REQ, SETTINGS, initial.pending
    )
    assert reply.itinerary is None and reply.pending
    assert plan.days[0].activities[0].place is None


@pytest.mark.parametrize(
    "text",
    [
        "第一天午餐预算改成80元",
        "第1天第1站改成11点",
        "第1天第1站换成室内活动",
        "确认第1天第1站的地点，记账30元",
    ],
)
async def test_budget_time_generic_refinement_and_mixed_record_are_not_place_edits(text):
    assert await handle_place_message(text, place_itinerary(), REQ, SETTINGS) is None


async def test_rejecting_all_candidates_then_naming_a_replacement_keeps_target(monkeypatch):
    new = candidates()[0].model_copy(update={"id": "new", "name": "新餐厅"})

    async def search(pending, hint, settings):
        assert pending.query_name == "新餐厅"
        return [new]

    monkeypatch.setattr("app.services.place_messages._search", search)
    plan = place_itinerary()
    initial = await handle_place_message("确认第1天第1站的地点", plan, REQ, SETTINGS)
    rejected = await handle_place_message("都不是", plan, REQ, SETTINGS, initial.pending)
    assert rejected.pending and not rejected.pending.candidates
    result = await handle_place_message("换成新餐厅", plan, REQ, SETTINGS, rejected.pending)
    assert result.itinerary.days[0].activities[0].place.id == "new"
    assert plan.days[0].activities[0].place is None


async def test_fork_manual_edits_and_new_topics_clear_the_place_question(environment):
    client, _, graph, _ = environment
    sid, bid, say = await seed(environment)
    await say("确认第1天第1站的地点")
    fork = await client.post(
        f"/api/sessions/{sid}/branches", json={"title": "另一个方案", "parent_branch_id": bid}
    )
    child = next(b for b in fork.json()["items"] if b["title"] == "另一个方案")
    assert (await graph.aget_state(thread_config(child["id"]))).values["pending_place"] is None
    assert (await graph.aget_state(thread_config(bid))).values["pending_place"]
    version = (await say("确认第1天第1站的地点"))["version_id"]
    response = await client.patch(
        f"/api/versions/{version}/days/1/activities/lunch/progress", json={"progress": "completed"}
    )
    assert response.status_code == 200, response.text
    assert (await graph.aget_state(thread_config(bid))).values["pending_place"] is None


async def test_explicit_wrong_location_can_correct_a_previously_matched_place():
    plan = place_itinerary()
    activity = plan.days[0].activities[0]
    activity.place = candidates()[0]
    activity.place_status = "matched"
    activity.transport_minutes = 60
    result = await handle_place_message("第1天第1站的地址在西城区那家", plan, REQ, SETTINGS)
    assert result.itinerary.days[0].activities[0].place.id == "west"
    assert result.itinerary.days[0].activities[0].transport_minutes is None
    assert plan.days[0].activities[0].place.id == "east"


async def test_stops_before_the_ontrip_origin_and_too_short_hints_do_not_move():
    plan = place_itinerary()
    initial = await handle_place_message("确认第1天第1站的地点", plan, REQ, SETTINGS)
    assert (
        await handle_place_message("区", plan, REQ, SETTINGS, initial.pending)
    ).itinerary is None
    plan.days[0].replan_context = ReplanContext(activity_id="hotel", time="15:00")
    reply = await handle_place_message("确认第1天第1站的地点", plan, REQ, SETTINGS)
    assert reply.itinerary is None and reply.pending is None


async def test_same_district_in_a_different_city_is_not_silently_selected(monkeypatch):
    plan = place_itinerary()
    plan.days[0].activities[0].candidates[0].city = "长春市"
    initial = await handle_place_message("确认第1天第1站的地点", plan, REQ, SETTINGS)

    async def none_found(*args):
        return []

    monkeypatch.setattr("app.services.place_messages._search", none_found)
    result = await handle_place_message("朝阳区那家", plan, REQ, SETTINGS, initial.pending)
    assert result.itinerary is None
    explicit = await handle_place_message("选第一个", plan, REQ, SETTINGS, initial.pending)
    assert explicit.itinerary.days[0].activities[0].place.city == "长春市"
