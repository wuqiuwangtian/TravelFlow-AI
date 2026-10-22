from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_itinerary_api import generate

from app.agents.checkpoint import build_serde, thread_config
from app.domain.itinerary import ExpenseCreate, Itinerary
from app.services.travel_service import preserve_travel_records


def expense(**updates):
    return {
        "title": "午餐",
        "amount": "86.35",
        "category": "meal",
        "day": 1,
        "request_id": "expense-test-0001",
        **updates,
    }


async def test_actual_expense_versions_export_and_idempotent_retry(environment):
    client, store, graph, _ = environment
    _, bid, before = await generate(client)
    vid = before["version"]["id"]
    response = await client.post(f"/api/versions/{vid}/expenses", json=expense())
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["itinerary"]["days"] == before["itinerary"]["days"]
    assert after["itinerary"]["expenses"][0]["amount_cents"] == 8635
    assert "86.35" in after["markdown"] and "不与上方规划估算相加" in after["markdown"]
    assert (await client.get(f"/api/versions/{vid}")).json() == before
    retried = await client.post(f"/api/versions/{vid}/expenses", json=expense())
    assert retried.status_code == 200
    assert retried.json()["version"]["id"] == after["version"]["id"]
    assert len(await store.list_versions(bid)) == 2
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["itinerary"].expenses[0].amount_cents == 8635
    for fmt in ("md", "html"):
        export = await client.get(f"/api/versions/{after['version']['id']}/export?format={fmt}")
        assert export.status_code == 200 and "86.35" in export.text
    changed = await client.post(f"/api/versions/{vid}/expenses", json=expense(amount="80"))
    assert changed.status_code == 409
    stale = await client.post(
        f"/api/versions/{vid}/expenses", json=expense(request_id="another-id")
    )
    assert stale.status_code == 409


async def test_expense_edit_delete_and_validation(environment):
    client, _, _, _ = environment
    _, _, before = await generate(client)
    vid = before["version"]["id"]
    for updates in (
        {"amount": "1.001"},
        {"amount": "-1"},
        {"amount": "NaN"},
        {"title": "   "},
        {"day": 9},
        {"activity_id": "absent"},
    ):
        response = await client.post(f"/api/versions/{vid}/expenses", json=expense(**updates))
        assert response.status_code in (400, 422), response.text
    created = (await client.post(f"/api/versions/{vid}/expenses", json=expense())).json()
    eid = created["itinerary"]["expenses"][0]["id"]
    edited = await client.put(
        f"/api/versions/{created['version']['id']}/expenses/{eid}", json=expense(amount="0.10")
    )
    assert edited.status_code == 200, edited.text
    after = edited.json()
    assert after["itinerary"]["expenses"][0]["amount_cents"] == 10
    removed = await client.delete(f"/api/versions/{after['version']['id']}/expenses/{eid}")
    assert removed.status_code == 200
    assert removed.json()["itinerary"]["expenses"] == []
    assert "0.00 元" in removed.json()["markdown"]
    assert len(created["itinerary"]["expenses"]) == 1


async def test_progress_and_expenses_survive_chat_revision_and_fork(environment):
    client, _, graph, _ = environment
    sid, bid, before = await generate(client)
    stop = before["itinerary"]["days"][0]["activities"][0]
    completed = await client.patch(
        f"/api/versions/{before['version']['id']}/days/1/activities/{stop['id']}/progress",
        json={"progress": "completed"},
    )
    assert completed.status_code == 200, completed.text
    detail = completed.json()
    assert "旅中状态：已完成" in detail["markdown"]
    created = await client.post(f"/api/versions/{detail['version']['id']}/expenses", json=expense())
    assert created.status_code == 200
    revised = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "第二天轻松一点"}
    )
    assert revised.status_code == 200, revised.text
    latest = (await client.get(f"/api/versions/{revised.json()['version_id']}")).json()
    assert latest["itinerary"]["expenses"][0]["amount_cents"] == 8635
    assert latest["itinerary"]["days"][0]["activities"][0]["progress"] == "completed"
    forked = await client.post(
        f"/api/sessions/{sid}/branches",
        json={
            "title": "旅中备选",
            "parent_branch_id": bid,
            "fork_version_id": latest["version"]["id"],
        },
    )
    assert forked.status_code == 200, forked.text
    fork = next(b for b in forked.json()["items"] if b["title"] == "旅中备选")
    snapshot = await graph.aget_state(thread_config(fork["id"]))
    assert snapshot.values["itinerary"].expenses[0].amount_cents == 8635
    serde = build_serde()
    restored = serde.loads_typed(serde.dumps_typed(snapshot.values))
    assert restored["itinerary"].expenses[0].amount_cents == 8635


def test_locked_history_missing_from_new_plan_is_restored():
    old = Itinerary.model_validate(
        {
            "days": [
                {
                    "day": 1,
                    "activities": [
                        {
                            "id": "a",
                            "title": "上午故宫",
                            "place_name": "故宫",
                            "progress": "completed",
                        },
                        {
                            "id": "b",
                            "title": "下午故宫",
                            "place_name": "故宫",
                            "progress": "skipped",
                        },
                    ],
                }
            ]
        }
    )
    new = Itinerary.model_validate(
        {
            "days": [
                {"day": 1, "activities": [{"id": "x", "title": "改过的故宫", "place_name": "故宫"}]}
            ]
        }
    )
    preserve_travel_records(old, new)
    assert [(a.id, a.progress) for a in new.days[0].activities] == [
        ("a", "completed"),
        ("b", "skipped"),
    ]
    assert old.days[0].activities[0].title == "上午故宫"


def test_money_is_validated_without_float_rounding():
    assert int(ExpenseCreate(**expense(amount="0.29")).amount * 100) == 29
    with pytest.raises(ValidationError):
        ExpenseCreate(**expense(amount=Decimal("0.001")))


async def test_chat_records_expense_and_progress_without_calling_model(environment, monkeypatch):
    client, _, _, _ = environment
    sid, bid, before = await generate(client)

    async def offline(*args, **kwargs):
        raise AssertionError("旅中记录不应调用模型")

    from conftest import FakeLLM

    monkeypatch.setattr(FakeLLM, "structured", offline)
    for text, expected in [("第2天第1站已完成", "已完成"), ("第2天午餐每人花了43.25元", "86.50元")]:
        response = await client.post(
            "/api/chat", json={"session_id": sid, "branch_id": bid, "message": text}
        )
        assert response.status_code == 200, response.text
        assert expected in response.json()["reply"]
    latest = (await client.get(f"/api/versions/{response.json()['version_id']}")).json()
    assert latest["itinerary"]["expenses"][0]["amount_cents"] == 8650
    assert latest["itinerary"]["days"][1]["activities"][0]["progress"] == "completed"
    assert (
        latest["itinerary"]["days"][0]["known_cost"] == before["itinerary"]["days"][0]["known_cost"]
    )
    changed = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "午餐记错了，改成80元"}
    )
    assert changed.status_code == 200, changed.text
    edited = (await client.get(f"/api/versions/{changed.json()['version_id']}")).json()
    assert len(edited["itinerary"]["expenses"]) == 1
    assert edited["itinerary"]["expenses"][0]["amount_cents"] == 8000


def test_travel_parser_never_converts_budget_or_question_to_actual():
    from app.domain.models import Requirements
    from app.services.travel_messages import handle_travel_message

    itinerary = Itinerary.model_validate(
        {
            "days": [
                {"day": 1, "activities": [{"id": "a", "title": "故宫", "place_name": "故宫博物院"}]}
            ]
        }
    )
    for message in [
        "午餐预计花了86元",
        "午餐花了86元吗？",
        "故宫还没有逛完",
        "故宫逛完后去哪里",
        "午餐花了两个小时",
    ]:
        assert handle_travel_message(message, itinerary, Requirements()) is None
    reply = handle_travel_message("午餐花了86元", itinerary, Requirements())
    assert reply.itinerary.expenses[0].day is None
    assert reply.itinerary.expenses[0].amount_cents == 8600
    assert itinerary.expenses == []
    invalid = handle_travel_message("午餐花了1.001元", itinerary, Requirements())
    assert invalid.itinerary is None
    assert "两位小数" in invalid.reply


async def test_organise_and_regenerate_keep_actual_records(environment, monkeypatch):
    client, _, graph, _ = environment
    from conftest import FakeLLM

    monkeypatch.setattr("app.api.routes_itinerary.create_llm_provider", lambda settings: FakeLLM())
    sid, bid, before = await generate(client)
    created = await client.post(f"/api/versions/{before['version']['id']}/expenses", json=expense())
    version = created.json()["version"]["id"]
    organised = await client.post(f"/api/versions/{version}/itinerary")
    assert organised.status_code == 200, organised.text
    assert organised.json()["itinerary"]["expenses"][0]["amount_cents"] == 8635
    from app.domain.enums import PlanStatus

    await graph.aupdate_state(thread_config(bid), {"status": PlanStatus.PLANNING})
    regenerated = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "重新生成"}
    )
    assert regenerated.status_code == 200, regenerated.text
    detail = (await client.get(f"/api/versions/{regenerated.json()['version_id']}")).json()
    assert detail["itinerary"]["expenses"][0]["amount_cents"] == 8635


def test_completed_activity_time_is_not_shifted_by_new_route():
    from app.domain.itinerary import DayPlan
    from app.services.itinerary_service import schedule_day

    day = DayPlan.model_validate(
        {
            "day": 1,
            "activities": [
                {"id": "a", "title": "出发", "start_time": "09:00", "duration_minutes": 300},
                {
                    "id": "b",
                    "title": "已逛完",
                    "start_time": "10:00",
                    "progress": "completed",
                    "duration_minutes": 30,
                },
            ],
        }
    )
    schedule_day(day)
    assert day.activities[1].start_time == "10:00"


def test_money_parser_handles_grouped_numbers_without_partial_amounts():
    from app.domain.models import Requirements
    from app.services.travel_messages import handle_travel_message

    itinerary = Itinerary.model_validate({"days": [{"day": 1}]})
    grouped = handle_travel_message("机票花了1,086.50元", itinerary, Requirements())
    assert grouped.itinerary.expenses[0].amount_cents == 108650
    for text in ["机票花了1e3元", "机票花了1,00元", "机票花了2.50.86元"]:
        assert handle_travel_message(text, itinerary, Requirements()).itinerary is None
