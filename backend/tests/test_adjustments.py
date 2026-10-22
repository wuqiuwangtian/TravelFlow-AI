from test_itinerary_api import generate

from app.agents.checkpoint import build_serde
from app.domain.itinerary import Itinerary


async def seeded(client):
    sid, bid, original = await generate(client)
    r = await client.post(
        f"/api/versions/{original['version']['id']}/expenses",
        json={"title": "门票", "amount": "200.00", "day": 2, "request_id": "source-expense-test"},
    )
    assert r.status_code == 200
    data = r.json()
    return sid, bid, data, data["itinerary"]["expenses"][0]["id"]


def adjustment(eid, **updates):
    return {
        "expense_id": eid,
        "kind": "refund",
        "amount": "50.00",
        "status": "pending",
        "request_id": "refund-request-test",
        **updates,
    }


async def test_pending_received_totals_and_idempotency(environment):
    client, store, _, _ = environment
    _, bid, original, eid = await seeded(client)
    vid = original["version"]["id"]
    r = await client.post(f"/api/versions/{vid}/expense-adjustments", json=adjustment(eid))
    assert r.status_code == 200, r.text
    pending = r.json()
    assert pending["itinerary"]["ledger_summary"]["pending_refund_cents"] == 5000
    assert pending["itinerary"]["ledger_summary"]["net_expense_cents"] == 20000
    repeated = await client.post(f"/api/versions/{vid}/expense-adjustments", json=adjustment(eid))
    assert repeated.status_code == 200
    assert repeated.json()["version"]["id"] == pending["version"]["id"]
    aid = pending["itinerary"]["adjustments"][0]["id"]
    settled = await client.put(
        f"/api/versions/{pending['version']['id']}/expense-adjustments/{aid}",
        json=adjustment(eid, status="received"),
    )
    assert settled.status_code == 200, settled.text
    received = settled.json()
    refund = received["itinerary"]["ledger_summary"]
    assert refund["pending_refund_cents"] == 0 and refund["refunded_cents"] == 5000
    assert refund["net_expense_cents"] == 15000
    reimb = await client.post(
        f"/api/versions/{received['version']['id']}/expense-adjustments",
        json=adjustment(
            eid,
            kind="reimbursement",
            amount="100",
            status="received",
            request_id="reimbursement-request",
        ),
    )
    assert reimb.status_code == 200, reimb.text
    latest = reimb.json()
    summary = latest["itinerary"]["ledger_summary"]
    assert summary["net_expense_cents"] == 15000 and summary["net_outlay_cents"] == 5000
    assert latest["itinerary"]["expenses"] == original["itinerary"]["expenses"]
    assert (await client.get(f"/api/versions/{vid}")).json() == original
    for fmt in ("md", "html"):
        exported = await client.get(f"/api/versions/{latest['version']['id']}/export?format={fmt}")
        assert exported.status_code == 200
        assert "1850.00" in exported.text and "150.00" in exported.text and "50.00" in exported.text
    serde = build_serde()
    plan = Itinerary.model_validate(latest["itinerary"])
    restored = serde.loads_typed(serde.dumps_typed(plan))
    assert restored.adjustments[0].amount_cents == 5000


async def test_overclaim_and_source_edits_are_guarded(environment):
    client, _, _, _ = environment
    _, _, original, eid = await seeded(client)
    vid = original["version"]["id"]
    for updates in (
        {"amount": "200.01"},
        {"amount": "-1"},
        {"amount": "0.001"},
        {"expense_id": "absent"},
    ):
        r = await client.post(
            f"/api/versions/{vid}/expense-adjustments", json=adjustment(eid, **updates)
        )
        assert r.status_code in (400, 422), r.text
    created = (
        await client.post(f"/api/versions/{vid}/expense-adjustments", json=adjustment(eid))
    ).json()
    now = created["version"]["id"]
    duplicate = await client.post(
        f"/api/versions/{vid}/expense-adjustments", json=adjustment(eid, amount="40")
    )
    assert duplicate.status_code == 409
    stale = await client.post(
        f"/api/versions/{vid}/expense-adjustments",
        json=adjustment(eid, request_id="another-request"),
    )
    assert stale.status_code == 409
    too_much = await client.post(
        f"/api/versions/{now}/expense-adjustments",
        json=adjustment(eid, amount="151", request_id="extra-refund"),
    )
    assert too_much.status_code == 400
    deletion = await client.delete(f"/api/versions/{now}/expenses/{eid}")
    assert deletion.status_code == 400
    reduction = await client.put(
        f"/api/versions/{now}/expenses/{eid}", json={"title": "门票", "amount": "40", "day": 2}
    )
    assert reduction.status_code == 400
    aid = created["itinerary"]["adjustments"][0]["id"]
    removed = await client.delete(f"/api/versions/{now}/expense-adjustments/{aid}")
    assert removed.status_code == 200
    assert removed.json()["itinerary"]["ledger_summary"]["pending_refund_cents"] == 0


async def test_chat_refund_and_reimbursement_without_model(environment, monkeypatch):
    from conftest import FakeLLM

    client, _, _, _ = environment
    sid, bid, before, eid = await seeded(client)

    async def offline(*args, **kwargs):
        raise AssertionError("回款不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    for text in ["第2天门票申请退款50元", "第2天门票退款到账50元", "第2天门票报销到账100元"]:
        r = await client.post(
            "/api/chat", json={"session_id": sid, "branch_id": bid, "message": text}
        )
        assert r.status_code == 200, r.text
    detail = (await client.get(f"/api/versions/{r.json()['version_id']}")).json()
    assert len(detail["itinerary"]["adjustments"]) == 2
    assert detail["itinerary"]["ledger_summary"]["net_outlay_cents"] == 5000
    assert detail["itinerary"]["expenses"] == before["itinerary"]["expenses"]


async def test_refinement_and_fork_preserve_adjustments(environment):
    client, store, graph, _ = environment
    sid, bid, before, eid = await seeded(client)
    paid = await client.post(
        f"/api/versions/{before['version']['id']}/expense-adjustments",
        json=adjustment(eid, status="received"),
    )
    r = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "第1天轻松一点"}
    )
    assert r.status_code == 200, r.text
    after = (await client.get(f"/api/versions/{r.json()['version_id']}")).json()
    assert after["itinerary"]["adjustments"] == paid.json()["itinerary"]["adjustments"]
    fork = await client.post(
        f"/api/sessions/{sid}/branches",
        json={
            "title": "回款历史",
            "parent_branch_id": bid,
            "fork_version_id": after["version"]["id"],
        },
    )
    assert fork.status_code == 200, fork.text
    branch = next(b for b in fork.json()["items"] if b["title"] == "回款历史")
    versions = await store.list_versions(branch["id"])
    assert (await store.get_version_detail(versions[-1].id)).itinerary.adjustments[
        0
    ].amount_cents == 5000
