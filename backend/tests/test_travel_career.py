from datetime import UTC, datetime, timedelta, timezone

from move_fixtures import move_itinerary
from test_activity_moves import seed
from test_journey_completion import close

from app.domain.itinerary import ExpenseAdjustment, ScheduledActivity
from app.domain.models import Requirements

END = datetime(2026, 10, 23, tzinfo=UTC)


async def snapshot(store, bid, plan):
    version = await store.create_version(
        bid, summary="记录更新", detail_level="L2_standard", trigger="test"
    )
    await store.save_plan(
        version.id,
        markdown="",
        itinerary=plan,
        requirements=Requirements(destination="尚未到访的计划城市"),
    )
    return version.id


async def trip(store, *, ended=END, title="旅行"):
    session = await store.create_session(title)
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    plan = move_itinerary()
    plan.ended_at = ended
    vid = await snapshot(store, branch.id, plan)
    return session.id, branch.id, vid, plan


async def career(client, query=""):
    response = await client.get("/api/travel-career" + query)
    assert response.status_code == 200, response.text
    return response.json()


async def test_empty_active_and_legacy_plans_do_not_count_as_finished(environment):
    client, store, _, _ = environment
    empty = await career(client)
    assert empty["summary"]["trip_count"] == empty["total"] == 0
    assert empty["cities"] == empty["items"] == []
    _, bid, vid, _ = await trip(store, ended=None)
    # 兼容尚未出现 ended_at 字段的历史 JSON。
    await store._conn.execute(
        "UPDATE plans SET itinerary=json_remove(itinerary,'$.ended_at') WHERE version_id=?", (vid,)
    )
    await store._conn.commit()
    assert (await career(client))["total"] == 0
    version = await store.create_version(
        bid, summary="纯文本", detail_level="L2_standard", trigger="test"
    )
    await store.save_plan(version.id, markdown="只写在文字里的已结束旅行")
    assert (await career(client))["total"] == 0
    assert (await client.get("/api/travel-career?page=0")).status_code == 400
    assert (await client.get("/api/travel-career?page_size=101")).status_code == 400


async def test_cities_only_from_completed_stays_not_transfers_anchors_or_plan_destination(
    environment,
):
    client, store, _, _ = environment
    _, bid, _, plan = await trip(store)
    plan.days[0].activities.extend(
        [
            ScheduledActivity(
                id="same-city", title="晚餐", kind="meal", city=" 北京市 ", progress="completed"
            ),
            ScheduledActivity(
                id="other-city", title="酒店", kind="hotel", city="天津市", progress="completed"
            ),
            ScheduledActivity(id="unknown", title="散步", progress="completed"),
            ScheduledActivity(
                id="transfer", title="换乘", kind="transport", city="上海", progress="completed"
            ),
            ScheduledActivity(
                id="anchor",
                title="定位起点",
                city="杭州",
                progress="completed",
                is_context_anchor=True,
            ),
            ScheduledActivity(id="skipped", title="没去", city="南京", progress="skipped"),
            ScheduledActivity(id="ongoing", title="尚未确认", city="苏州", progress="in_progress"),
        ]
    )
    # 已确认地点的城市优先于旧活动文本。
    park = plan.days[1].activities[0]
    park.progress = "completed"
    park.city = "文本旧城市"
    park.place.city = "北京市"
    plan.days[0].activities[1].progress = "completed"  # 车站也不作为到访城市
    vid = await snapshot(store, bid, plan)
    result = await career(client)
    assert result["summary"]["completed_count"] == 7
    assert result["summary"]["unlocated_completed_count"] == 1
    assert result["cities"] == [
        {"name": "北京", "trip_count": 1},
        {"name": "天津", "trip_count": 1},
    ]
    assert result["items"][0]["version_id"] == vid
    assert result["items"][0]["cities"] == ["北京", "天津"]


async def test_real_ledger_excludes_estimates_pending_and_keeps_reimbursement_separate(environment):
    client, store, _, _ = environment
    _, bid, _, plan = await trip(store)
    expense = plan.expenses[0]
    for i, (kind, status, cents) in enumerate(
        [
            ("refund", "received", 501),
            ("reimbursement", "received", 300),
            ("refund", "pending", 200),
            ("reimbursement", "pending", 100),
        ]
    ):
        plan.adjustments.append(
            ExpenseAdjustment(
                id=f"adjust-{i}",
                expense_id=expense.id,
                kind=kind,
                status=status,
                amount_cents=cents,
                created_at=END,
                updated_at=END,
            )
        )
    await snapshot(store, bid, plan)
    _, bid2, _, no_expenses = await trip(store)
    no_expenses.expenses = []
    await snapshot(store, bid2, no_expenses)
    result = await career(client)
    assert result["summary"]["expense_count"] == 1
    assert result["summary"]["trips_without_expenses"] == 1
    assert result["summary"]["unsettled_count"] == 2
    assert result["summary"]["ledger"] == {
        "paid_cents": 2000,
        "refunded_cents": 501,
        "reimbursed_cents": 300,
        "pending_refund_cents": 200,
        "pending_reimbursement_cents": 100,
        "net_expense_cents": 1499,
        "net_outlay_cents": 1199,
        "over_recovered_cents": 0,
    }
    assert result["cities"] == [{"name": "北京", "trip_count": 2}]


async def test_one_session_one_trip_prefers_finished_primary_then_latest_finished(environment):
    client, store, _, _ = environment
    sid, bid, primary_vid, plan = await trip(store)
    child = await store.create_branch(sid, "最近结束的备选")
    plan.ended_at = END + timedelta(hours=1)
    plan.expenses[0].amount_cents = 9999
    child_vid = await snapshot(store, child.id, plan)
    result = await career(client)
    assert result["total"] == 1 and result["summary"]["ledger"]["paid_cents"] == 2000
    assert result["items"][0]["version_id"] == primary_vid
    assert result["items"][0]["source_basis"] == "primary"
    assert result["items"][0]["finished_branch_count"] == 2
    # 重新开启主方案，不退回历史已结束版本，而改用当前仍结束的备选。
    plan.ended_at = None
    await snapshot(store, bid, plan)
    result = await career(client)
    assert result["items"][0]["version_id"] == child_vid
    assert result["items"][0]["source_basis"] == "latest_finished"
    assert result["summary"]["ledger"]["paid_cents"] == 9999
    await store.delete_branch(child.id)
    assert (await career(client))["total"] == 0


async def test_primary_switch_changes_source_without_merging_or_duplicating(environment):
    client, store, _, _ = environment
    sid, bid, _, plan = await trip(store)
    child = await store.create_branch(sid, "另一份实际记录")
    plan.days[0].activities[0].city = "广州"
    other = await snapshot(store, child.id, plan)
    response = await client.post(f"/api/branches/{child.id}/primary")
    assert response.status_code == 200
    result = await career(client)
    assert result["total"] == 1 and result["items"][0]["version_id"] == other
    assert result["cities"] == [{"name": "广州", "trip_count": 1}]
    # 归档只隐藏会话；删除才移除生涯记录。
    await client.patch(f"/api/sessions/{sid}", json={"archived": True})
    assert (await career(client))["items"][0]["session_archived"]
    await client.delete(f"/api/sessions/{sid}")
    assert (await career(client))["summary"]["trip_count"] == 0


async def test_fallback_uses_actual_instant_and_stable_branch_tie_break(environment):
    client, store, _, _ = environment
    sid, _, _, plan = await trip(store, ended=None)
    for bid, ended in [
        ("z-tied", END.isoformat()),
        ("a-tied", END.astimezone(tz=timezone(timedelta(hours=8))).isoformat()),
        ("later-looking", "2026-10-23T01:00:00+08:00"),
    ]:
        await store.create_branch(sid, bid, branch_id=bid)
        plan.ended_at = datetime.fromisoformat(ended)
        await snapshot(store, bid, plan)
    chosen = (await career(client))["items"][0]
    assert chosen["branch_id"] == "a-tied"
    assert chosen["finished_branch_count"] == 3
    assert chosen["source_basis"] == "latest_finished"


async def test_post_end_corrections_reopen_and_history_reflect_current_record(environment):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    ended = await close(client, (await store.list_versions(bid))[-1].id)
    end_id = ended["version"]["id"]
    response = await client.patch(
        f"/api/versions/{end_id}/days/2/activities/park/progress", json={"progress": "completed"}
    )
    assert response.status_code == 200
    current = response.json()
    result = await career(client)
    assert result["summary"]["completed_count"] == 2
    assert result["items"][0]["version_id"] == current["version"]["id"]
    await close(client, current["version"]["id"], False)
    assert (await career(client))["summary"]["trip_count"] == 0
    assert (await client.get(f"/api/versions/{end_id}/recap")).json()["ended_at"]


async def test_totals_include_all_trips_across_pages_and_city_filter_only_filters_sources(
    environment,
):
    client, store, _, _ = environment
    for i in range(21):
        _, bid, _, plan = await trip(store, ended=END + timedelta(days=i))
        if i == 20:
            plan.days[0].activities[0].city = "成都"
            await snapshot(store, bid, plan)
    first = await career(client)
    second = await career(client, "?page=2")
    assert first["total"] == second["total"] == first["summary"]["trip_count"] == 21
    assert first["summary"] == second["summary"]
    assert first["summary"]["ledger"]["net_expense_cents"] == 42000
    assert len(first["items"]) == 20 and len(second["items"]) == 1
    assert first["items"][0]["cities"] == ["成都"]
    assert not {t["session_id"] for t in first["items"]} & {
        t["session_id"] for t in second["items"]
    }
    filtered = await career(client, "?city=成都")
    assert filtered["total"] == 1 and filtered["summary"] == first["summary"]
    assert filtered["items"][0] == first["items"][0]
    assert (await career(client, "?city=未到访"))["items"] == []
