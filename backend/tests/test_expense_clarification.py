import json

import pytest
from conftest import MARKDOWN, FakeLLM, requirements
from expense_fixtures import expense_itinerary

from app.agents.checkpoint import build_serde, thread_config
from app.agents.nodes.refiner import RefineIntent
from app.agents.state import initial_state
from app.domain.clarification import clarification_prompt
from app.domain.enums import PlanStatus
from app.domain.itinerary import AdjustmentCreate, ExpenseCreate
from app.domain.models import Requirements
from app.services.adjustment_service import add_adjustment
from app.services.chat_service import ChatService
from app.services.itinerary_service import sync_markdown
from app.services.journey_clarification import resume_journey_message
from app.services.journey_messages import prepare_journey_message
from app.services.travel_service import add_expense


async def setup(environment, *, with_receipt=False):
    client, store, graph, _ = environment
    session = await store.create_session("消费候选验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    itinerary = expense_itinerary()
    if with_receipt:
        # 只验证已有凭证元数据的保留，不访问真实文件。
        itinerary.expenses[1].receipt_ids = ["00000000000000000000000000000001"]
    req = requirements().model_copy(update={"duration_days": 2})
    values = {
        **initial_state(),
        "requirements": req,
        "itinerary": itinerary,
        "plan_markdown": sync_markdown(MARKDOWN, itinerary, req),
        "status": PlanStatus.REVIEWING,
    }
    config = await graph.aupdate_state(thread_config(branch.id), values, as_node="planner")
    await ChatService(graph=graph, store=store).record_version(branch.id, values, config)

    async def say(message, *, request_id=None, stream=False):
        payload = {"session_id": session.id, "branch_id": branch.id, "message": message}
        if request_id:
            payload["request_id"] = request_id
        response = await client.post("/api/chat/stream" if stream else "/api/chat", json=payload)
        assert response.status_code == 200, response.text
        if stream:
            return json.loads(response.text.split("event: done\ndata: ")[-1].split("\n\n")[0])
        return response.json()

    return client, store, graph, session.id, branch.id, say


async def test_same_day_same_amount_choices_survive_refresh_and_preserve_metadata(
    environment, monkeypatch
):
    client, store, graph, _, bid, say = await setup(environment, with_receipt=True)

    async def offline(*args, **kwargs):
        raise AssertionError("精确消费选择不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    result = await say("第1天咖啡消费改成18.50元", stream=True)
    choices = result["clarification"]
    assert choices["kind"] == "expense" and len(choices["options"]) == 2
    assert "上午在餐厅" in choices["options"][0]["label"]
    assert "下午在酒店" in choices["options"][1]["label"]
    assert all("20.00元" in option["label"] for option in choices["options"])
    assert "fixture-coffee" not in json.dumps(choices)
    assert (await client.get(f"/api/branches/{bid}/messages")).json()["clarification"] == choices
    pending = (await graph.aget_state(thread_config(bid))).values["pending_journey"]
    assert build_serde().loads_typed(build_serde().dumps_typed(pending)) == pending
    assert len(await store.list_versions(bid)) == 1
    result = await say("选第二笔", stream=True)
    assert result["clarification"] is None and "下午在酒店" in result["reply"]
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.expenses[0] == before.expenses[0]
    assert current.expenses[1].amount_cents == 1850
    assert current.expenses[1].model_dump(exclude={"amount_cents", "updated_at"}) == (
        before.expenses[1].model_dump(exclude={"amount_cents", "updated_at"})
    )
    assert len(await store.list_versions(bid)) == 2


@pytest.mark.parametrize("operation", ["delete", "refund", "reimbursement"])
async def test_delete_and_recovery_apply_only_to_selected_expense(environment, operation):
    _, store, graph, _, bid, say = await setup(environment)
    command = {
        "delete": "删除第1天咖啡消费",
        "refund": "第1天咖啡退款到账5元",
        "reimbursement": "第1天咖啡报销到账5元",
    }[operation]
    assert (await say(command))["clarification"]["kind"] == "expense"
    await say("选第2笔")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    if operation == "delete":
        assert [e.id for e in current.expenses] == ["fixture-coffee-1"]
    else:
        assert [e.amount_cents for e in current.expenses] == [2000, 2000]
        assert len(current.adjustments) == 1
        assert current.adjustments[0].expense_id == "fixture-coffee-2"
        assert current.adjustments[0].kind == operation
        assert current.adjustments[0].amount_cents == 500
    assert len(await store.list_versions(bid)) == 2


async def test_choice_can_be_followed_by_amount_then_another_choice_atomically(environment):
    _, store, graph, _, bid, say = await setup(environment)
    assert (await say("第1天咖啡消费改成0元；咖啡退款到账"))["clarification"]["kind"] == "expense"
    assert (await say("选第2笔"))["clarification"]["kind"] == "amount"
    assert (await say("18元"))["clarification"]["kind"] == "expense"
    assert (await say("选第1笔"))["clarification"]["kind"] == "amount"
    assert len(await store.list_versions(bid)) == 1
    assert len((await graph.aget_state(thread_config(bid))).values["itinerary"].adjustments) == 0
    await say("5元")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert [e.amount_cents for e in current.expenses] == [2000, 1800]
    assert current.adjustments[0].expense_id == "fixture-coffee-1"
    assert current.adjustments[0].amount_cents == 500
    assert len(await store.list_versions(bid)) == 2


def test_replay_can_select_new_draft_expense_and_preserve_selection_after_later_failure():
    itinerary = expense_itinerary()
    text = "第1天咖啡花了30元；咖啡消费改成25元；地铁花了0元"
    pending = prepare_journey_message(text, itinerary, Requirements()).clarification
    assert len(pending.expense_options) == 3
    assert pending.expense_options[2].expense_id is None
    second = resume_journey_message("选第3笔", pending, itinerary, Requirements())
    assert second.itinerary is None and second.clarification.kind == "amount"
    done = resume_journey_message("5元", second.clarification, itinerary, Requirements())
    assert [e.amount_cents for e in done.itinerary.expenses] == [2000, 2000, 2500, 500]
    assert len(itinerary.expenses) == 2


def test_replay_of_prior_delete_keeps_correct_position_for_next_choice():
    itinerary = expense_itinerary()
    add_expense(
        itinerary,
        ExpenseCreate(title="咖啡", amount="30", day=1, request_id="third-coffee"),
    )
    pending = prepare_journey_message(
        "第1天删除咖啡消费；咖啡退款到账5元", itinerary, Requirements()
    ).clarification
    second = resume_journey_message("选第1笔", pending, itinerary, Requirements())
    assert second.clarification.expense_options[0].expense_id == "fixture-coffee-2"
    done = resume_journey_message("选第2笔", second.clarification, itinerary, Requirements())
    assert [e.id for e in done.itinerary.expenses] == ["fixture-coffee-2", "third-coffee"]
    assert done.itinerary.adjustments[0].expense_id == "third-coffee"


async def test_cross_day_then_same_day_choice_keeps_following_clause_day(environment):
    _, store, graph, _, bid, say = await setup(environment)
    await say("第2天咖啡花了30元")
    assert (await say("咖啡消费改成18元；第2天第1站已完成"))["clarification"]["kind"] == "day"
    assert (await say("第1天"))["clarification"]["kind"] == "expense"
    await say("选第2笔")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert [(e.day, e.amount_cents) for e in current.expenses] == [(1, 2000), (1, 1800), (2, 3000)]
    assert current.days[1].activities[0].progress == "completed"
    assert len(await store.list_versions(bid)) == 3


async def test_cancel_and_invalid_number_do_not_save_or_discard_pending(environment):
    _, store, graph, _, bid, say = await setup(environment)
    await say("第1天第1站已完成；咖啡消费改成18元")
    wrong = await say("选第9笔")
    assert wrong["clarification"]["kind"] == "expense"
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.days[0].activities[0].progress == "not_started"
    await say("取消这次操作")
    assert (await graph.aget_state(thread_config(bid))).values["pending_journey"] is None
    assert len(await store.list_versions(bid)) == 1


@pytest.mark.parametrize("answer", ["第2笔吗？", "如果选第2笔", "第2笔附近有什么", "确认"])
async def test_questions_and_new_topics_never_apply_expense_choice(
    environment, monkeypatch, answer
):
    _, store, graph, _, bid, say = await setup(environment)
    await say("咖啡消费改成18元")

    async def unclear(*args, **kwargs):
        return RefineIntent(intent="unclear")

    monkeypatch.setattr(FakeLLM, "structured", unclear)
    result = await say(answer)
    assert result["clarification"] is None
    assert [
        e.amount_cents
        for e in (await graph.aget_state(thread_config(bid))).values["itinerary"].expenses
    ] == [2000, 2000]
    assert len(await store.list_versions(bid)) == 1


def test_pagination_exposes_every_candidate_without_changing_the_ledger():
    itinerary = expense_itinerary()
    for index in range(9):
        add_expense(
            itinerary,
            ExpenseCreate(title="咖啡", amount="10", day=1, request_id=f"many-coffee-{index}"),
        )
    pending = prepare_journey_message("咖啡消费改成8元", itinerary, Requirements()).clarification
    assert len(clarification_prompt(pending)["options"]) == 9
    next_page = resume_journey_message("下一页消费", pending, itinerary, Requirements())
    assert next_page.itinerary is None
    options = clarification_prompt(next_page.clarification)["options"]
    assert options[0]["reply"] == "选第9笔" and options[-1]["reply"] == "上一页消费"
    assert len(options) == 4
    done = resume_journey_message("选第11笔", next_page.clarification, itinerary, Requirements())
    assert done.itinerary.expenses[-1].amount_cents == 800
    assert all(e.amount_cents == 1000 for e in done.itinerary.expenses[2:-1])


def test_undated_candidates_offer_selection_and_changed_source_invalidates_choice():
    itinerary = expense_itinerary()
    itinerary.expenses[0].day = None
    pending = prepare_journey_message("咖啡消费改成18元", itinerary, Requirements()).clarification
    assert pending.kind == "expense" and "付款日未关联" in pending.expense_options[0].label
    changed = itinerary.model_copy(deep=True)
    changed.expenses.reverse()
    assert resume_journey_message("选第2笔", pending, changed, Requirements()) is None


def test_selected_record_still_checks_refund_limits_and_deletion_protection():
    itinerary = expense_itinerary()
    add_adjustment(
        itinerary,
        AdjustmentCreate(
            expense_id="fixture-coffee-2",
            amount="15",
            kind="refund",
            status="received",
            request_id="protected-refund",
        ),
    )
    for command, error in [
        ("咖啡消费改成10元", "小于已记录"),
        ("删除咖啡消费", "已有退款"),
        ("咖啡退款到账10元", "超过"),
    ]:
        pending = prepare_journey_message(command, itinerary, Requirements()).clarification
        result = resume_journey_message("选第2笔", pending, itinerary, Requirements())
        assert result.itinerary is None and error in result.reply
    assert itinerary.expenses[1].amount_cents == 2000 and len(itinerary.adjustments) == 1


async def test_choice_request_retry_does_not_add_recovery_twice(environment):
    client, store, graph, _, bid, say = await setup(environment)
    await say("第1天咖啡退款到账5元")
    first = await say("选第2笔", request_id="choice-retry-01", stream=True)
    assert await say("选第2笔", request_id="choice-retry-01") == first
    await say("第2天地铁花了5元")
    assert await say("选第2笔", request_id="choice-retry-01", stream=True) == first
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert len(current.adjustments) == 1
    assert current.adjustments[0].expense_id == "fixture-coffee-2"
    assert len(await store.list_versions(bid)) == 3
    assert (await client.get(f"/api/branches/{bid}/messages")).json()["clarification"] is None
