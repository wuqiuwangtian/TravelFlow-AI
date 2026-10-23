import json

import pytest
from move_fixtures import move_itinerary
from test_activity_moves import REQ, seed

from app.agents.checkpoint import thread_config
from app.core.errors import InvalidRequest, LLMAuthenticationFailed, LLMOutputInvalid, LLMTimeout
from app.domain.itinerary import TravelRecap
from app.services.recap_writing import RecapDraftInput, RecapWriting, generate_recap_writing


class Writer:
    def __init__(self):
        self.materials = None

    async def structured(self, messages, schema, **kwargs):
        assert schema is RecapWriting
        assert all(not message.images for message in messages)
        self.materials = json.loads(messages[-1].content)
        assert "原始记录" in messages[0].content
        return RecapWriting(title="走过的故宫", text="故宫的一段漫步，成了这次旅程值得回味的片段。")


@pytest.mark.parametrize("spending,memory", [(False, ""), (True, ""), (False, "手写的回忆")])
async def test_generation_materials_only_include_completed_and_explicit_options(spending, memory):
    plan = move_itinerary(with_receipt=True)
    plan.recap = TravelRecap(
        title="旧标题", body="不能自动发送的旧回忆", source_fingerprint="x", social_copy="私密文案"
    )
    before = plan.model_dump_json()
    writer = Writer()
    await generate_recap_writing(
        writer,
        plan,
        REQ,
        RecapDraftInput(
            kind="recap", source_fingerprint="0" * 64, memory=memory, include_spending=spending
        ),
    )
    data = writer.materials
    assert len(data["completed_activities"]) == 1
    assert data["completed_activities"][0]["place"] == "故宫"
    encoded = json.dumps(data, ensure_ascii=False)
    for private in [
        "公园漫步",
        "北京站",
        "预付公园门票",
        "不能自动发送的旧回忆",
        "私密文案",
        plan.expenses[0].receipt_ids[0],
        plan.expenses[0].notes,
    ]:
        assert private not in encoded
    assert ("user_memory" in data) == bool(memory)
    assert ("recorded_spending" in data) == spending
    if spending:
        assert data["recorded_spending"]["summary"]["net_expense_cents"] == 2000
    assert plan.model_dump_json() == before


async def test_empty_facts_requires_material_and_does_not_call_model():
    plan = move_itinerary()
    for day in plan.days:
        for a in day.activities:
            a.progress = "not_started"

    class NoCalls:
        async def structured(self, *args, **kwargs):
            raise AssertionError("没有素材时不调用模型")

    with pytest.raises(InvalidRequest):
        await generate_recap_writing(
            NoCalls(), plan, REQ, RecapDraftInput(kind="social", source_fingerprint="0" * 64)
        )
    writer = Writer()
    result = await generate_recap_writing(
        writer,
        plan,
        REQ,
        RecapDraftInput(kind="social", source_fingerprint="0" * 64, memory="只是想记录自己的心情"),
    )
    assert result.text and writer.materials["completed_activities"] == []


async def test_oversized_social_output_is_rejected_without_truncating_user_text():
    class Long:
        async def structured(self, *args, **kwargs):
            return RecapWriting(title="标题", text="字" * 2001)

    with pytest.raises(LLMOutputInvalid):
        await generate_recap_writing(
            Long(),
            move_itinerary(),
            REQ,
            RecapDraftInput(kind="social", source_fingerprint="0" * 64),
        )


async def test_api_draft_is_read_only_save_preserves_photos_history_and_legacy_social(
    environment, monkeypatch
):
    from test_travel_photos import upload_photo

    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    photo = await upload_photo(client, vid)
    view = (await client.get(f"/api/versions/{vid}/recap")).json()
    writer = Writer()
    monkeypatch.setattr("app.api.routes_itinerary.create_llm_provider", lambda settings: writer)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    draft = await client.post(
        f"/api/versions/{vid}/recap/draft",
        json={"kind": "social", "source_fingerprint": view["source_fingerprint"]},
    )
    assert draft.status_code == 200, draft.text
    assert len(await store.list_versions(bid)) == 1
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == before
    payload = {k: view[k] for k in ("title", "body", "source_fingerprint")} | {
        "social_copy": draft.json()["text"],
        "photos": [{"id": photo, "included": True}],
        "cover_photo_id": photo,
    }
    saved = await client.put(f"/api/versions/{vid}/recap", json=payload)
    assert saved.status_code == 200, saved.text
    new = saved.json()["version"]["id"]
    assert (await client.put(f"/api/versions/{vid}/recap", json=payload)).json()["version"][
        "id"
    ] == new
    restored = (await client.get(f"/api/versions/{new}/recap")).json()
    assert restored["social_copy"] == draft.json()["text"] and restored["photos"][0]["id"] == photo
    assert not (await client.get(f"/api/versions/{vid}/recap")).json()["social_copy"]
    legacy = {k: restored[k] for k in ("title", "body", "source_fingerprint")}
    legacy["body"] = "后来补写的回忆"
    edited = await client.put(f"/api/versions/{new}/recap", json=legacy)
    assert edited.status_code == 200, edited.text
    assert edited.json()["itinerary"]["recap"]["social_copy"] == draft.json()["text"]
    assert edited.json()["itinerary"]["recap"]["cover_photo_id"] == photo


@pytest.mark.parametrize("failure", [LLMAuthenticationFailed, LLMTimeout, LLMOutputInvalid])
async def test_model_failures_never_save_any_version(environment, monkeypatch, failure):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    view = (await client.get(f"/api/versions/{vid}/recap")).json()

    class Failed:
        async def structured(self, *args, **kwargs):
            raise failure()

    monkeypatch.setattr("app.api.routes_itinerary.create_llm_provider", lambda settings: Failed())
    result = await client.post(
        f"/api/versions/{vid}/recap/draft",
        json={"kind": "recap", "source_fingerprint": view["source_fingerprint"]},
    )
    assert result.status_code in {422, 502, 504}
    assert len(await store.list_versions(bid)) == 1


async def test_changed_version_while_generating_rejects_old_draft(environment, monkeypatch):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    view = (await client.get(f"/api/versions/{vid}/recap")).json()

    class Changed(Writer):
        async def structured(self, *args, **kwargs):
            r = await client.patch(
                f"/api/versions/{vid}/days/2/activities/park/progress",
                json={"progress": "completed"},
            )
            assert r.status_code == 200
            return await super().structured(*args, **kwargs)

    monkeypatch.setattr("app.api.routes_itinerary.create_llm_provider", lambda settings: Changed())
    result = await client.post(
        f"/api/versions/{vid}/recap/draft",
        json={"kind": "recap", "source_fingerprint": view["source_fingerprint"]},
    )
    assert result.status_code == 409, result.text
    assert len(await store.list_versions(bid)) == 2

    class NoCalls:
        async def structured(self, *args, **kwargs):
            raise AssertionError("旧版本不能调用模型")

    monkeypatch.setattr("app.api.routes_itinerary.create_llm_provider", lambda settings: NoCalls())
    assert (
        await client.post(
            f"/api/versions/{vid}/recap/draft",
            json={"kind": "recap", "source_fingerprint": view["source_fingerprint"]},
        )
    ).status_code == 409
