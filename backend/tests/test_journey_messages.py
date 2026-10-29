import pytest
from conftest import FakeLLM, drafts
from test_itinerary_api import generate

from app.agents.checkpoint import thread_config
from app.agents.nodes.refiner import RefineIntent, RevisionOutput
from app.core.errors import LLMUnavailable
from app.domain.itinerary import Itinerary
from app.domain.models import Requirements
from app.services.journey_messages import prepare_journey_message


def plan():
    return Itinerary.model_validate(
        {
            "days": [
                {"day": 1, "activities": [{"id": "a", "title": "故宫", "place_name": "故宫博物院"}]}
            ]
        }
    )


def test_planning_estimates_and_questions_still_use_the_normal_conversation():
    for text in ["午餐预计花了86元", "第二天轻松一点，午餐预计花了86元", "午餐花了86元吗？"]:
        assert prepare_journey_message(text, plan(), Requirements()) is None


@pytest.mark.parametrize(
    "text",
    [
        "第1天故宫逛完了，午餐花了1.001元",
        "第1天午餐花了86元，地铁花了5元吗？",
        "第1天午餐花了86元，门票退款到账20元",
        "第1天午餐花了86元，撤销刚才的全部操作",
        "第1天午餐花了86元，如果故宫逛完了",
        "第1天午餐花了86元，地铁花了5元，地铁消费改成0元",
        "第1天午餐花了86元，门票预计花了20元",
    ],
)
def test_any_unclear_or_invalid_item_leaves_everything_untouched(text):
    original = plan()
    before = original.model_dump()
    result = prepare_journey_message(text, original, Requirements())
    assert result.itinerary is None and "没有保存任何操作" in result.reply
    assert original.model_dump() == before


def test_expense_names_grouped_numbers_and_ordered_references():
    original = plan()
    result = prepare_journey_message(
        "第1天，咖啡和甜点花了30元并且午餐花了1,086.50元；午餐记错了，改成86.35元；午餐退款到账20元",
        original,
        Requirements(),
    )
    assert result.itinerary is not None, result.reply
    assert [(e.title, e.amount_cents, e.day) for e in result.itinerary.expenses] == [
        ("咖啡和甜点", 3000, 1),
        ("午餐", 8635, 1),
    ]
    assert result.itinerary.ledger_summary.net_expense_cents == 9635
    assert not original.expenses


async def test_batch_records_across_days_once_without_model_and_survives_fork(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    sid, bid, before = await generate(client)

    async def offline(*args, **kwargs):
        raise AssertionError("明确的旅中批量记录不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    response = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": (
                "第1天第1站已完成，午餐花了86.35元，地铁花了5元；"
                "第2天第2站已预约16:00，停留60分钟；"
                "第1天午餐报销到账30元，午餐退款到账20元"
            ),
        },
    )
    assert response.status_code == 200, response.text
    latest = (await client.get(f"/api/versions/{response.json()['version_id']}")).json()
    itinerary = latest["itinerary"]
    assert itinerary["days"][0]["activities"][0]["progress"] == "completed"
    assert itinerary["days"][1]["activities"][1]["booking"]["start_time"] == "16:00"
    assert itinerary["days"][2] == before["itinerary"]["days"][2]
    assert len(itinerary["expenses"]) == 2 and len(itinerary["adjustments"]) == 2
    assert itinerary["ledger_summary"]["net_outlay_cents"] == 4135
    assert len(await store.list_versions(bid)) == 2  # 六项操作只新增一个版本。
    assert (await graph.aget_state(thread_config(bid))).values["revision"] == 1
    for fmt in ("md", "html"):
        exported = await client.get(f"/api/versions/{latest['version']['id']}/export?format={fmt}")
        assert "41.35" in exported.text and "已完成" in exported.text
    forked = await client.post(
        f"/api/sessions/{sid}/branches",
        json={
            "title": "批量操作后分叉",
            "parent_branch_id": bid,
            "fork_version_id": latest["version"]["id"],
        },
    )
    branch = next(b for b in forked.json()["items"] if b["title"] == "批量操作后分叉")
    snapshot = await graph.aget_state(thread_config(branch["id"]))
    assert snapshot.values["itinerary"].ledger_summary.net_outlay_cents == 4135


async def test_batch_and_model_revision_preserve_new_history_and_reply_to_all_items(
    environment, monkeypatch
):
    client, _, graph, _ = environment
    sid, bid, _ = await generate(client)

    async def revise(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            assert "轻松" in messages[0].content
            return RefineIntent(intent="modify")
        assert schema is RevisionOutput
        assert '"progress":"completed"' in messages[0].content
        assert "86.35" in messages[0].content
        return RevisionOutput(markdown="调整后", days=drafts())

    monkeypatch.setattr(FakeLLM, "structured", revise)
    response = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第1天第1站已完成，午餐花了86.35元，后面安排轻松一点",
        },
    )
    assert response.status_code == 200, response.text
    assert all(t in response.json()["reply"] for t in ("已完成", "86.35", "调整"))
    current = (await graph.aget_state(thread_config(bid))).values
    assert current["revision"] == 1
    assert current["itinerary"].days[0].activities[0].progress == "completed"
    assert current["itinerary"].expenses[0].amount_cents == 8635


async def test_model_failure_does_not_save_the_records_before_the_revision(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    sid, bid, before = await generate(client)

    async def fail(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            return RefineIntent(intent="modify")
        raise LLMUnavailable("测试调整失败")

    monkeypatch.setattr(FakeLLM, "structured", fail)
    response = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第1天第1站已完成，午餐花了86元，后面安排轻松一点",
        },
    )
    assert response.status_code == 502
    assert len(await store.list_versions(bid)) == 1
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["itinerary"].model_dump(mode="json") == before["itinerary"]
    followup = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第1天午餐花了86元，地铁花了5元",
        },
    )
    assert followup.status_code == 200
    latest = (await client.get(f"/api/versions/{followup.json()['version_id']}")).json()
    assert len(latest["itinerary"]["expenses"]) == 2


async def test_error_after_earlier_items_keeps_graph_and_export_unchanged(environment):
    client, _, graph, _ = environment
    sid, bid, before = await generate(client)
    response = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第1天第1站已完成，午餐花了86元，午餐退款到账100元",
        },
    )
    assert response.status_code == 200
    assert "没有保存任何操作" in response.json()["reply"]
    assert (await graph.aget_state(thread_config(bid))).values["revision"] == 0
    latest = (await client.get(f"/api/versions/{response.json()['version_id']}")).json()
    assert latest["itinerary"] == before["itinerary"]
    assert latest["markdown"] == before["markdown"]


@pytest.mark.parametrize("verb", ["实付", "实付了"])
def test_actual_payment_word_records_amount_and_cleans_expense_title(verb):
    result = prepare_journey_message(f"第1天午餐{verb}86.35元", plan(), Requirements(travelers=2))
    assert result.itinerary is not None, result.reply
    expense = result.itinerary.expenses[0]
    assert (expense.title, expense.amount_cents, expense.day) == ("午餐", 8635, 1)


@pytest.mark.parametrize(
    "text",
    [
        "午餐尚未实付86元",
        "午餐待实付86元",
        "午餐应实付86元",
        "午餐预计实付86元",
        "午餐实付86元吗？",
        "午餐实付金额待核实86元",
    ],
)
def test_unconfirmed_actual_payment_does_not_partially_apply_batch(text):
    original = plan()
    result = prepare_journey_message(f"第1天第1站已完成，{text}", original, Requirements())
    assert result.itinerary is None
    assert "没有保存任何操作" in result.reply
    assert not original.expenses
    assert original.days[0].activities[0].progress == "not_started"


async def test_actual_payment_with_completion_and_booking_is_atomic_and_replayable(
    environment, monkeypatch
):
    client, store, _, _ = environment
    sid, bid, _ = await generate(client)

    async def offline(*args, **kwargs):
        raise AssertionError("明确的实付、完成与预约不应交给模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "request_id": "actual-payment-batch-once",
        "message": "第1天第1站已完成，第1天午餐实付86.35元，第2天第1站已预约09:00，停留90分钟",
    }
    response = await client.post("/api/chat", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["version_id"]
    latest = (await client.get(f"/api/versions/{result['version_id']}")).json()
    assert latest["itinerary"]["expenses"][0]["amount_cents"] == 8635
    assert latest["itinerary"]["expenses"][0]["title"] == "午餐"
    assert latest["itinerary"]["days"][0]["activities"][0]["progress"] == "completed"
    assert latest["itinerary"]["days"][1]["activities"][0]["booking"]["start_time"] == "09:00"
    replay = (await client.post("/api/chat", json=payload)).json()
    assert replay["version_id"] == result["version_id"]
    assert len(await store.list_versions(bid)) == 2


@pytest.mark.parametrize(
    "message",
    [
        "第二天轻松一点，原来的实付和人工预算都保持。",
        "第二天轻松一点，保留已有实付、账目和人工预算。",
        "第二天轻松一点，其他天的安排、留园09:00的预约、已经完成的活动、实付和人工预算都保持。",
    ],
)
def test_replanning_financial_preservation_is_not_a_new_expense(message):
    assert prepare_journey_message(message, plan(), Requirements()) is None


@pytest.mark.parametrize(
    "operation",
    [
        "午餐实付86元",
        "午餐实付86",
        "午餐实付金额86",
        "标记第1天第1站已完成",
    ],
)
def test_preservation_cannot_silently_discard_a_new_record(operation):
    original = plan()
    result = prepare_journey_message(f"保留实付记录同时{operation}", original, Requirements())
    assert result is not None
    assert result.itinerary is None
    assert not original.expenses


@pytest.mark.parametrize("operation", ["午餐退款到账10元", "午餐消费改成80元"])
def test_station_prefixed_expense_can_be_found_without_repeating_ordinal(operation):
    original = plan()
    recorded = prepare_journey_message("第1天第1站午餐实付86.35元", original, Requirements())
    assert recorded.itinerary is not None
    expense = recorded.itinerary.expenses[0]
    assert expense.title == "第1站午餐"
    result = prepare_journey_message(f"第1天{operation}", recorded.itinerary, Requirements())
    assert result.itinerary is not None, result.reply
    assert result.itinerary.expenses[0].id == expense.id
    assert result.itinerary.expenses[0].title == "第1站午餐"
    if "退款" in operation:
        assert result.itinerary.adjustments[0].expense_id == expense.id
        assert result.itinerary.ledger_summary.net_expense_cents == 7635
    else:
        assert result.itinerary.expenses[0].amount_cents == 8000
    assert expense.amount_cents == 8635  # 原快照不受原位操作影响。


def test_same_named_station_expenses_still_require_selection_and_respect_explicit_station():
    recorded = prepare_journey_message(
        "第1天第1站午餐实付86元，第1天第2站午餐实付50元", plan(), Requirements()
    )
    assert recorded.itinerary is not None
    result = prepare_journey_message("第1天午餐退款到账10元", recorded.itinerary, Requirements())
    assert result.itinerary is None
    assert result.clarification.kind == "expense"
    assert len(result.clarification.expense_options) == 2
    result = prepare_journey_message(
        "第1天第3站午餐退款到账10元", recorded.itinerary, Requirements()
    )
    assert result.itinerary is None
    assert not recorded.itinerary.adjustments


@pytest.mark.parametrize("title,other", [("车", "停车费"), ("票", "门票")])
def test_single_character_station_title_does_not_match_unrelated_expense(title, other):
    recorded = prepare_journey_message(f"第1天第1站{title}实付86元", plan(), Requirements())
    result = prepare_journey_message(
        f"第1天{other}退款到账10元", recorded.itinerary, Requirements()
    )
    assert result.itinerary is None
    assert not recorded.itinerary.adjustments
