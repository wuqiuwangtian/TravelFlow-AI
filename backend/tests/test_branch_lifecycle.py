import pytest
from test_activity_moves import seed
from test_journey_completion import close

from app.agents.checkpoint import thread_config
from app.services.itinerary_service import sync_markdown


async def latest(store, branch_id):
    versions = await store.list_versions(branch_id)
    assert versions
    return await store.get_version_detail(versions[-1].id)


async def checked(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("checkpoint", ["absent", "stale", "missing"])
async def test_latest_fork_uses_saved_finances_even_without_current_checkpoint(
    environment, checkpoint
):
    client, store, graph, _ = environment
    sid, bid, _ = await seed(environment)
    await close(client, (await latest(store, bid)).version.id)
    previous = await latest(store, bid)
    saved = previous.model_copy(deep=True)
    saved.itinerary.budget_items[0].planned_cents = 12550
    saved.itinerary.expenses[0].amount_cents = 2025
    saved.itinerary.recap.body = "已保存的手写回忆"
    saved.markdown = sync_markdown(saved.markdown, saved.itinerary, saved.requirements)
    # 模拟旧库导入或 checkpoint 遗失：业务快照仍是最新的完整记录。
    version = await store.create_version(
        bid,
        checkpoint_id="missing-checkpoint" if checkpoint == "missing" else None,
        summary="已保存的最新记录",
    )
    await store.save_plan(
        version.id,
        markdown=saved.markdown,
        itinerary=saved.itinerary,
        requirements=saved.requirements,
        citations=saved.citations,
        todos=saved.todos,
        searched_at=saved.searched_at,
    )
    original = await store.get_version_detail(version.id)
    if checkpoint == "absent":
        await graph.checkpointer.adelete_thread(bid)
    response = await client.post(
        f"/api/sessions/{sid}/branches", json={"title": "记录备选", "parent_branch_id": bid}
    )
    assert response.status_code == 200, response.text
    child = next(b for b in response.json()["items"] if b["title"] == "记录备选")
    copied = await latest(store, child["id"])
    expected = original.itinerary.model_copy(deep=True, update={"ended_at": None})
    assert copied.itinerary == expected
    assert copied.markdown == original.markdown
    assert copied.requirements == original.requirements
    assert child["fork_version_id"] == original.version.id
    assert (await graph.aget_state(thread_config(child["id"]))).values["itinerary"] == expected
    assert await store.get_version_detail(original.version.id) == original
    assert (await client.get("/api/recap-archive")).json()["total"] == 1


async def test_missing_explicit_fork_version_does_not_create_an_empty_or_unrelated_branch(
    environment,
):
    client, store, _, _ = environment
    sid, bid, _ = await seed(environment)
    before = await store.list_branches(sid)
    response = await client.post(
        f"/api/sessions/{sid}/branches",
        json={"title": "错误来源", "parent_branch_id": bid, "fork_version_id": "missing-version"},
    )
    assert response.status_code == 400, response.text
    assert "版本不存在" in response.json()["error"]["message"]
    assert await store.list_branches(sid) == before


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("amount, cents", [("0", 0), (None, None)])
async def test_fork_finances_remain_separate_through_finish_primary_switch_and_reopen(
    environment, historical, amount, cents
):
    client, store, graph, _ = environment
    sid, bid, _ = await seed(environment)
    ended = await close(client, (await latest(store, bid)).version.id)
    historical_id = ended["version"]["id"]
    history_exports = {
        fmt: (await client.get(f"/api/versions/{historical_id}/export?format={fmt}")).text
        for fmt in ("md", "html")
    }
    priced = await checked(
        await client.put(
            f"/api/versions/{historical_id}/budget-items/stay-park", json={"amount": "125.50"}
        )
    )
    paid = await checked(
        await client.post(
            f"/api/versions/{priced['version']['id']}/expenses",
            json={
                "title": "原方案补记实付",
                "amount": "12.34",
                "day": 1,
                "category": "meal",
                "request_id": "parent-payment",
            },
        )
    )
    refunded = await checked(
        await client.post(
            f"/api/versions/{paid['version']['id']}/expense-adjustments",
            json={
                "expense_id": "fixture-linked-expense",
                "amount": "5",
                "kind": "refund",
                "status": "received",
                "request_id": "parent-refund",
            },
        )
    )
    recap = await checked(await client.get(f"/api/versions/{refunded['version']['id']}/recap"))
    await checked(
        await client.put(
            f"/api/versions/{refunded['version']['id']}/recap",
            json={
                "title": "原方案回顾",
                "body": "原方案手写回忆",
                "source_fingerprint": recap["source_fingerprint"],
            },
        )
    )
    parent = await latest(store, bid)
    payload = {"title": "财务独立备选", "parent_branch_id": bid}
    if historical:
        payload["fork_version_id"] = historical_id
    branches = await checked(await client.post(f"/api/sessions/{sid}/branches", json=payload))
    child = next(b for b in branches["items"] if b["title"] == payload["title"])
    child_id = child["id"]
    source_id = historical_id if historical else parent.version.id
    source = await store.get_version_detail(source_id)
    copied = await latest(store, child_id)
    assert copied.itinerary == source.itinerary.model_copy(deep=True, update={"ended_at": None})
    assert child["fork_version_id"] == source_id
    assert (await client.get("/api/recap-archive")).json()["total"] == 1
    changed = await checked(
        await client.put(
            f"/api/versions/{copied.version.id}/budget-items/stay-park", json={"amount": amount}
        )
    )
    await checked(
        await client.post(
            f"/api/versions/{changed['version']['id']}/expenses",
            json={
                "title": "备选独立实付",
                "amount": "6.78",
                "day": 2,
                "category": "ticket",
                "activity_id": "park",
                "budget_item_id": "stay-park",
                "request_id": "child-payment",
            },
        )
    )
    child_ended = await close(client, (await latest(store, child_id)).version.id)
    child_plan = child_ended["itinerary"]
    assert child_plan["budget_items"][0]["planned_cents"] == cents
    assert child_plan["expenses"][:-1] == source.itinerary.model_dump(mode="json")["expenses"]
    assert child_plan["adjustments"] == source.itinerary.model_dump(mode="json")["adjustments"]
    assert child_plan["recap"] == source.itinerary.model_dump(mode="json")["recap"]
    assert child_plan["days"][1]["activities"][0]["cost"] == (
        None if cents is None else cents / 100
    )
    assert child_plan["expenses"][-1]["day"] == 2
    assert child_plan["ledger_summary"]["net_expense_cents"] == (2678 if historical else 3412)
    assert await store.get_version_detail(parent.version.id) == parent
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == parent.itinerary
    assert (await client.get("/api/recap-archive")).json()["total"] == 2

    async def career_source(expected_branch, expected_net, basis):
        result = await checked(await client.get("/api/travel-career"))
        assert result["summary"]["trip_count"] == 1
        assert result["summary"]["ledger"]["net_expense_cents"] == expected_net
        assert result["items"][0]["branch_id"] == expected_branch
        assert result["items"][0]["source_basis"] == basis

    await career_source(bid, 2734, "primary")
    await checked(await client.post(f"/api/branches/{child_id}/primary"))
    await career_source(child_id, 2678 if historical else 3412, "primary")
    reopened_child = await close(client, child_ended["version"]["id"], False)
    assert reopened_child["itinerary"] == {**child_plan, "ended_at": None}
    await career_source(bid, 2734, "latest_finished")
    reopened_parent = await close(client, parent.version.id, False)
    assert (
        reopened_parent["itinerary"]["recap"] == parent.itinerary.model_dump(mode="json")["recap"]
    )
    assert (await client.get("/api/travel-career")).json()["total"] == 0
    assert (await client.get("/api/recap-archive")).json()["total"] == 0
    await close(client, reopened_child["version"]["id"])
    await checked(await client.post(f"/api/branches/{bid}/primary"))
    await career_source(child_id, 2678 if historical else 3412, "latest_finished")
    await close(client, reopened_parent["version"]["id"])
    await career_source(bid, 2734, "primary")
    assert (await client.get("/api/recap-archive")).json()["total"] == 2
    assert (await client.get(f"/api/versions/{historical_id}")).json() == ended
    for fmt, text in history_exports.items():
        assert (await client.get(f"/api/versions/{historical_id}/export?format={fmt}")).text == text
