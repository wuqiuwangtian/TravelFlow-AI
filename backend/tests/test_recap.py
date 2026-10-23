import pytest
from move_fixtures import move_itinerary
from test_activity_moves import REQ, seed

from app.agents.checkpoint import build_serde, thread_config
from app.domain.itinerary import TravelRecap
from app.services.adjustment_messages import handle_adjustment_message
from app.services.recap_service import recap_view, render_recap
from app.services.travel_service import preserve_travel_records


def test_recap_only_counts_actual_visits_and_net_payments_without_private_receipts():
    plan = move_itinerary(with_receipt=True)
    plan.days[1].activities[1].progress = "skipped"
    refund = handle_adjustment_message("第2天预付公园门票退款到账5元", plan)
    assert refund and refund.itinerary
    plan = refund.itinerary
    view = recap_view(plan, REQ)
    assert view["ledger"]["net_expense_cents"] == 1500
    assert view["days"][0]["completed"] == ["已逛完的故宫（故宫）"]
    assert "公园漫步" not in view["template"]
    assert "预约展览" in view["days"][1]["skipped"][0]
    assert view["days"][0]["unconfirmed"]
    assert view["unsettled_count"] > 0 and not view["saved"]
    output = render_recap(view)
    assert "¥15.00" in output and "未确认到访" in output
    assert plan.expenses[0].receipt_ids[0] not in output
    assert plan.expenses[0].notes not in output


def test_recap_survives_regeneration_and_facts_change_marks_stale_without_rewriting_text():
    plan = move_itinerary()
    view = recap_view(plan, REQ)
    plan.recap = TravelRecap(
        title="我的回忆", body="难忘的一天", source_fingerprint=view["source_fingerprint"]
    )
    assert not recap_view(plan, REQ)["stale"]
    copied = plan.model_copy(deep=True)
    copied.recap = None
    preserve_travel_records(plan, copied)
    assert copied.recap == plan.recap
    copied.days[1].activities[0].progress = "completed"
    updated = recap_view(copied, REQ)
    assert updated["stale"] and updated["body"] == "难忘的一天"
    assert "公园漫步" in updated["template"]
    assert build_serde().loads_typed(build_serde().dumps_typed(copied)) == copied


def test_html_export_escapes_user_and_place_markup():
    plan = move_itinerary()
    plan.days[0].activities[0].title = "<script>alert(1)</script>"
    view = recap_view(plan, REQ)
    view["body"] = '<img src=x onerror="alert(2)">'
    result = render_recap(view, as_html=True)
    assert "<script>" not in result and "<img" not in result
    assert "&lt;script&gt;" in result and "&lt;img" in result


async def test_recap_api_preview_save_retry_refresh_history_and_export(environment):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    view = (await client.get(f"/api/versions/{vid}/recap")).json()
    assert len(await store.list_versions(bid)) == 1
    payload = {k: view[k] for k in ("title", "body", "source_fingerprint")}
    payload["body"] = "我的手写回忆"
    result = await client.put(f"/api/versions/{vid}/recap", json=payload)
    assert result.status_code == 200, result.text
    saved = result.json()
    new_id = saved["version"]["id"]
    assert (await client.put(f"/api/versions/{vid}/recap", json=payload)).json()["version"][
        "id"
    ] == new_id
    assert len(await store.list_versions(bid)) == 2
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.expenses == before.expenses and current.days == before.days
    assert (await client.get(f"/api/versions/{new_id}/recap")).json()["body"] == "我的手写回忆"
    assert not (await client.get(f"/api/versions/{vid}/recap")).json()["saved"]
    output = await client.get(f"/api/versions/{new_id}/recap/export?format=html")
    assert "我的手写回忆" in output.text and "sandbox" in output.headers["content-security-policy"]
    changed = {**payload, "body": "另一个回忆"}
    assert (await client.put(f"/api/versions/{vid}/recap", json=changed)).status_code == 409


@pytest.mark.parametrize("change", ["activity", "expense"])
async def test_concurrent_facts_change_rejects_save_without_partial_version(environment, change):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    view = (await client.get(f"/api/versions/{vid}/recap")).json()
    if change == "expense":
        response = await client.put(
            f"/api/versions/{vid}/expenses/fixture-linked-expense",
            json={"title": "门票", "amount": "18", "category": "ticket", "day": 2},
        )
    else:
        response = await client.patch(
            f"/api/versions/{vid}/days/2/activities/park/progress", json={"progress": "completed"}
        )
    assert response.status_code == 200, response.text
    payload = {k: view[k] for k in ("title", "body", "source_fingerprint")}
    result = await client.put(f"/api/versions/{vid}/recap", json=payload)
    assert result.status_code == 409, result.text
    assert len(await store.list_versions(bid)) == 2
