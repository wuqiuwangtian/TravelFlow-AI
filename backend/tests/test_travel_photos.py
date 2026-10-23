import base64
from io import BytesIO

import pytest
from PIL import Image
from test_activity_moves import seed
from test_receipts import receipt_image, upload

from app.agents.checkpoint import build_serde, thread_config
from app.core.errors import InvalidRequest
from app.services.photo_service import normalise_photo, photo_export_html


async def upload_photo(client, vid, color="white"):
    if color == "white":
        raw = receipt_image()
    else:
        out = BytesIO()
        Image.new("RGB", (300, 200), color).save(out, format="PNG")
        raw = out.getvalue()
    response = await client.post(f"/api/versions/{vid}/travel-photos", content=raw)
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def payload(client, vid, photos, cover=None):
    view = (await client.get(f"/api/versions/{vid}/recap")).json()
    return {k: view[k] for k in ("title", "body", "source_fingerprint")} | {
        "photos": photos,
        "cover_photo_id": cover,
    }


def test_photo_copy_removes_metadata_and_bounds_dimensions():
    image, width, height, _ = normalise_photo(receipt_image())
    assert (width, height) == (480, 320)
    assert b"private metadata" not in image
    with Image.open(BytesIO(image)) as clean:
        assert not clean.getexif() and clean.format == "JPEG"
    out = BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(out, format="PNG")
    _, width, height, _ = normalise_photo(out.getvalue())
    assert max(width, height) == 1200


@pytest.mark.parametrize(
    "raw", [b"", b"broken", b'<svg onload="alert(1)"></svg>', b"x" * (4 * 1024 * 1024 + 1)]
)
def test_invalid_or_oversized_photo_is_rejected(raw):
    with pytest.raises(InvalidRequest):
        normalise_photo(raw)


async def test_photos_are_separate_deduplicated_owned_and_deleted_with_trip(environment):
    client, store, _, _ = environment
    sid, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    photo = await upload_photo(client, vid)
    assert await upload_photo(client, vid) == photo
    receipt = await upload(client, vid)
    assert receipt != photo and await store.get_photo(sid, receipt) is None
    image = await client.get(f"/api/versions/{vid}/travel-photos/{photo}")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert b"private metadata" not in image.content
    assert len(await store.list_versions(bid)) == 1
    sid2, bid2, _ = await seed(environment)
    vid2 = (await store.list_versions(bid2))[-1].id
    assert (await client.get(f"/api/versions/{vid2}/travel-photos/{photo}")).status_code == 400
    assert await store.get_photo(sid2, photo) is None
    await client.delete(f"/api/sessions/{sid}")
    assert await store.get_photo(sid, photo) is None


async def test_photo_version_cover_selection_export_and_removal_preserve_history(environment):
    client, store, graph, _ = environment
    sid, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    cover = await upload_photo(client, vid)
    second = await upload_photo(client, vid, "blue")
    private = await upload_photo(client, vid, "red")
    data = await payload(
        client,
        vid,
        [
            {
                "id": cover,
                "caption": "封面<script>bad</script>",
                "included": True,
                "day": 1,
                "activity_id": "park",
            },
            {"id": second, "caption": "精选蓝色", "included": True},
            {"id": private, "caption": "不导出的私密说明", "included": False},
        ],
        cover,
    )
    response = await client.put(f"/api/versions/{vid}/recap", json=data)
    assert response.status_code == 200, response.text
    new = response.json()["version"]["id"]
    assert (await client.put(f"/api/versions/{vid}/recap", json=data)).json()["version"][
        "id"
    ] == new
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.days == before.days and current.expenses == before.expenses
    assert build_serde().loads_typed(build_serde().dumps_typed(current)) == current
    assert current.recap.cover_photo_id == cover
    text_only = (await client.get(f"/api/versions/{new}/recap/export?format=html")).text
    assert "data:image/jpeg" not in text_only and "不导出的私密说明" not in text_only
    output = await client.get(f"/api/versions/{new}/recap/export?format=html&include_photos=true")
    assert output.status_code == 200
    assert output.text.count("<img ") == 2 and "<script>" not in output.text
    assert "&lt;script&gt;" in output.text and "不导出的私密说明" not in output.text
    assert "img-src data:" in output.headers["content-security-policy"]
    assert output.text.count('aria-label="回顾封面"') == 1
    hidden = await store.get_photo(sid, private)
    assert base64.b64encode(hidden["image_data"]).decode() not in output.text
    removed = await client.put(f"/api/versions/{new}/recap", json=await payload(client, new, []))
    assert removed.status_code == 200
    removed_id = removed.json()["version"]["id"]
    assert not (await client.get(f"/api/versions/{removed_id}/recap")).json()["photos"]
    assert len((await client.get(f"/api/versions/{new}/recap")).json()["photos"]) == 3
    assert (await client.get(f"/api/versions/{new}/travel-photos/{cover}")).status_code == 200
    assert (
        await client.post(f"/api/versions/{new}/travel-photos", content=receipt_image())
    ).status_code == 409
    # 旧客户端只更改文字时，不能清空已经保存的照片和封面。
    legacy = {k: data[k] for k in ("title", "body", "source_fingerprint")}
    legacy["body"] = "补写一段"
    # 用保存照片的历史分叉继续，验证图片仍归属于同一次旅行。
    branch = (
        await client.post(
            f"/api/sessions/{sid}/branches",
            json={"title": "回忆分支", "parent_branch_id": bid, "fork_version_id": new},
        )
    ).json()
    fork = next(b for b in branch["items"] if b["title"] == "回忆分支")
    versions = await store.list_versions(fork["id"])
    fork_vid = versions[-1].id
    saved = await client.put(f"/api/versions/{fork_vid}/recap", json=legacy)
    assert saved.status_code == 200, saved.text
    assert len(saved.json()["itinerary"]["recap"]["photos"]) == 3
    assert saved.json()["itinerary"]["recap"]["cover_photo_id"] == cover


@pytest.mark.parametrize(
    "reason", ["foreign", "receipt", "missing_activity", "bad_day", "duplicate", "bad_cover"]
)
async def test_bad_photo_metadata_does_not_save_partial_recap(environment, reason):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    photo = await upload_photo(client, vid)
    if reason == "foreign":
        _, other_bid, _ = await seed(environment)
        photo = await upload_photo(client, (await store.list_versions(other_bid))[-1].id)
    elif reason == "receipt":
        photo = await upload(client, vid)
    item = {"id": photo, "included": True}
    if reason == "missing_activity":
        item["activity_id"] = "missing"
    if reason == "bad_day":
        item["day"] = 50
    photos = [item, item] if reason == "duplicate" else [item]
    data = await payload(client, vid, photos, "0" * 32 if reason == "bad_cover" else None)
    response = await client.put(f"/api/versions/{vid}/recap", json=data)
    assert response.status_code in {400, 422}, response.text
    assert len(await store.list_versions(bid)) == 1


async def test_photo_count_quota_and_duplicate_retry_at_limit(environment):
    _, store, _, _ = environment
    sid = (await store.create_session("照片额度")).id
    first = None
    for i in range(100):
        row = await store.save_photo(sid, str(i), b"image", 1, 1)
        first = first or row
    assert (await store.save_photo(sid, "0", b"image", 1, 1))["id"] == first["id"]
    with pytest.raises(InvalidRequest):
        await store.save_photo(sid, "overflow", b"image", 1, 1)


async def test_export_size_bound(environment, monkeypatch):
    from move_fixtures import move_itinerary

    from app.domain.itinerary import RecapPhoto, TravelRecap

    _, store, _, _ = environment
    plan = move_itinerary()
    plan.recap = TravelRecap(
        title="回忆",
        body="",
        source_fingerprint="x",
        photos=[RecapPhoto(id="0" * 32, included=True)],
    )

    async def large(*args):
        return {"image_data": b"x" * (16 * 1024 * 1024 + 1)}

    monkeypatch.setattr(store, "get_photo", large)
    with pytest.raises(InvalidRequest):
        await photo_export_html("<main></main>", plan, store, "trip")
