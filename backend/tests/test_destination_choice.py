from datetime import date

import pytest
from langchain_core.messages import HumanMessage

from app.agents.destination_choice import destination_choice
from app.agents.nodes.collector import (
    CollectResult,
    DestinationIdea,
    DestinationOptions,
    make_collector_node,
)
from app.core.errors import LLMOutputInvalid
from app.domain.enums import PlanStatus
from app.domain.models import Requirements


@pytest.mark.parametrize(
    "text,city",
    [
        ("第二个", "重庆"),
        ("选２", "重庆"),
        ("那就第 二 个吧！", "重庆"),
        ("我选择成都", "成都"),
        ("就选成都吧", "成都"),
        ("那就去重庆", "重庆"),
        ("成都。", "成都"),
        ("我想去重庆吧", "重庆"),
    ],
)
def test_exact_saved_city_choices(text, city):
    assert destination_choice(text, ["成都", "重庆"]) == (city, False)


@pytest.mark.parametrize("text", ["第三个", "选0", "第10个"])
def test_invalid_ordinal_is_not_a_new_recommendation(text):
    assert destination_choice(text, ["成都", "重庆"]) == (None, True)


@pytest.mark.parametrize(
    "text",
    [
        "第二个怎么样？",
        "不要第二个",
        "成都还是重庆",
        "选第二个，预算改成3000",
        "2天",
        "预算2千",
        "再推荐几个",
    ],
)
def test_questions_negation_and_compound_input_are_not_short_choices(text):
    assert destination_choice(text, ["成都", "重庆"]) == (None, False)


class NoCalls:
    async def structured(self, *args, **kwargs):
        raise AssertionError("Saved short choices must not ask the model to choose again")

    async def search(self, *args, **kwargs):
        raise AssertionError("Invalid choices must not restart recommendation search")


def choice_state(text):
    return {
        "requirements": Requirements(
            origin="上海",
            duration_days=3,
            budget=2500,
            travelers=2,
            budget_basis="total",
            departure_date=date(2026, 11, 12),
            preferences=["少走路"],
        ),
        "messages": [HumanMessage(content=text)],
        "destination_ideas": ["成都", "重庆"],
        "destination_recommendation_requested": True,
    }


async def test_short_choice_keeps_all_conditions_and_skips_model():
    state = choice_state("第二个")
    result = await make_collector_node(NoCalls(), NoCalls())(state)
    assert result["status"] == PlanStatus.RESEARCHING
    assert result["requirements"] == state["requirements"].model_copy(
        update={"destination": "重庆"}
    )
    assert state["requirements"].destination is None
    assert not result["destination_recommendation_requested"]


async def test_bad_ordinal_keeps_candidates_and_collected_conditions():
    state = choice_state("第三个")
    result = await make_collector_node(NoCalls(), NoCalls())(state)
    assert result["status"] == PlanStatus.COLLECTING
    assert result["requirements"] == state["requirements"]
    assert result["destination_ideas"] == state["destination_ideas"]
    assert "1. 成都" in result["messages"][0].content and "2. 重庆" in result["messages"][0].content


async def test_compound_choice_keeps_model_extraction_for_new_conditions():
    class Model:
        async def structured(self, messages, schema, **kwargs):
            assert schema is CollectResult
            assert "重庆" in messages[0].content
            return CollectResult(
                extracted=Requirements(destination="重庆", budget=3000), reply="按新预算继续"
            )

    state = choice_state("选第二个，预算改成3000")
    result = await make_collector_node(Model())(state)
    assert result["requirements"].destination == "重庆"
    assert result["requirements"].budget == 3000
    assert result["requirements"].departure_date == date(2026, 11, 12)


async def test_empty_recommendation_is_an_error_not_an_empty_choice_list():
    class Model:
        async def structured(self, messages, schema, **kwargs):
            if schema is CollectResult:
                return CollectResult(reply="准备推荐")
            return DestinationOptions(ideas=[DestinationIdea(city="  ", reason="空城市")])

    state = choice_state("换一批")
    with pytest.raises(LLMOutputInvalid, match="没有生成可用"):
        await make_collector_node(Model())(state)


async def test_city_list_trims_blanks_and_duplicate_names():
    class Model:
        async def structured(self, messages, schema, **kwargs):
            if schema is CollectResult:
                return CollectResult(reply="准备推荐")
            return DestinationOptions(
                ideas=[
                    DestinationIdea(city=" 成都 ", reason=" 美食 "),
                    DestinationIdea(city="成都", reason="重复城市"),
                    DestinationIdea(city="重庆", reason="夜景"),
                ]
            )

    result = await make_collector_node(Model())(choice_state("换一批"))
    assert result["destination_ideas"] == ["成都", "重庆"]
    assert "1. 成都：美食" in result["messages"][0].content


async def test_city_choice_replay_keeps_one_plan_and_does_not_call_collector_again(
    environment, monkeypatch
):
    from conftest import FakeLLM

    client, _, graph, _ = environment
    original = FakeLLM.structured
    calls = []

    async def structured(self, messages, schema, **kwargs):
        if schema is CollectResult:
            calls.append(messages[-1].content)
            if len(calls) > 1:
                raise AssertionError("A persisted short choice must bypass extraction")
            return CollectResult(
                extracted=Requirements(
                    origin="上海", duration_days=3, budget=2500, travelers=2, preferences=["少走路"]
                ),
                reply="先看城市",
                wants_destination_ideas=True,
            )
        if schema is DestinationOptions:
            return DestinationOptions(
                ideas=[
                    DestinationIdea(city="成都", reason="美食"),
                    DestinationIdea(city="重庆", reason="夜景"),
                ]
            )
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    created = (await client.post("/api/sessions", json={})).json()
    sid = created["session"]["id"]
    bid = created["branches"][0]["id"]
    result = await client.post(
        "/api/chat",
        json={"session_id": sid, "message": "从上海出发，3天，两人预算2500，少走路，推荐城市"},
    )
    assert result.status_code == 200 and result.json()["version_id"] is None
    history = await client.get(f"/api/branches/{bid}/messages")
    assert "重庆" in str(history.json())
    invalid = await client.post("/api/chat", json={"session_id": sid, "message": "第三个"})
    assert invalid.status_code == 200 and invalid.json()["version_id"] is None
    data = {"session_id": sid, "message": "第二个", "request_id": "city-choice-request"}
    first = await client.post("/api/chat", json=data)
    assert first.status_code == 200
    again = await client.post("/api/chat", json=data)
    assert again.json()["version_id"] == first.json()["version_id"]
    saved = (await client.get(f"/api/sessions/{sid}")).json()
    assert len(saved["versions"][bid]) == 1
    detail = (await client.get(f"/api/versions/{first.json()['version_id']}")).json()
    assert (
        detail["requirements"]["destination"] == "重庆" and detail["requirements"]["budget"] == 2500
    )
    assert len(calls) == 1


async def test_empty_city_reply_can_retry_with_original_request_and_keep_conditions(
    environment, monkeypatch
):
    from conftest import FakeLLM

    client, _, _, _ = environment
    original = FakeLLM.structured
    recommendations = 0

    async def structured(self, messages, schema, **kwargs):
        nonlocal recommendations
        if schema is CollectResult:
            return CollectResult(
                extracted=Requirements(budget=2000)
                if "预算" in messages[-1].content
                else Requirements(origin="上海", duration_days=3),
                reply="先收集条件",
                wants_destination_ideas=True,
            )
        if schema is DestinationOptions:
            recommendations += 1
            return DestinationOptions(
                ideas=[]
                if recommendations == 1
                else [DestinationIdea(city="北京", reason="城市漫步")]
            )
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    created = (await client.post("/api/sessions", json={})).json()
    sid = created["session"]["id"]
    bid = created["branches"][0]["id"]
    await client.post("/api/chat", json={"session_id": sid, "message": "上海出发，玩3天，推荐城市"})
    payload = {"session_id": sid, "message": "预算2000元", "request_id": "empty-city-options"}
    failed = await client.post("/api/chat", json=payload)
    assert failed.status_code == 422 and "没有生成可用" in failed.text
    state = (await client.get(f"/api/sessions/{sid}")).json()
    assert not state["versions"][bid]
    retried = await client.post("/api/chat", json=payload)
    assert retried.status_code == 200 and "北京" in retried.json()["reply"]
    selected = await client.post("/api/chat", json={"session_id": sid, "message": "第一个"})
    assert selected.status_code == 200 and selected.json()["version_id"]
    detail = (await client.get(f"/api/versions/{selected.json()['version_id']}")).json()
    assert detail["requirements"]["origin"] == "上海" and detail["requirements"]["budget"] == 2000
