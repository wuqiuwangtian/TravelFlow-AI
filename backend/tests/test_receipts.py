from io import BytesIO

import pytest
from PIL import Image
from test_itinerary_api import generate

from app.core.errors import LLMUnavailable
from app.domain.itinerary import ReceiptDraft
from app.llm.base import ChatMessage
from app.llm.openai_compat import _to_langchain, _with_json_hint
from app.services.receipt_service import normalise_receipt, recognise_receipt


def receipt_image():
    out = BytesIO()
    image = Image.new("RGB", (480, 320), "white")
    exif = Image.Exif()
    exif[0x010E] = "private metadata"
    image.save(out, format="PNG", exif=exif)
    return out.getvalue()


async def upload(client, vid):
    r = await client.post(
        f"/api/versions/{vid}/receipts",
        content=receipt_image(),
        headers={"content-type": "image/png"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_preview_removes_metadata_and_preserves_multimodal_hint():
    raw = receipt_image()
    preview, mime, width, height, _ = normalise_receipt(raw)
    assert mime == "image/png" and (width, height) == (480, 320)
    assert Image.open(BytesIO(preview)).getexif() == {}
    assert b"private metadata" not in preview
    message = ChatMessage(
        role="user", content="读取图片", images=["data:image/jpeg;base64,aGVsbG8="]
    )
    updated = _with_json_hint([message], ReceiptDraft)
    assert updated[0].images == message.images
    payload = _to_langchain(updated)[0].content
    assert payload[1]["image_url"]["url"] == message.images[0]


async def test_receipt_roundtrip_owner_and_duplicate_expense(environment):
    client, store, _, _ = environment
    sid, bid, before = await generate(client)
    vid = before["version"]["id"]
    rid = await upload(client, vid)
    assert await upload(client, vid) == rid
    preview = await client.get(f"/api/versions/{vid}/receipts/{rid}")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert b"private metadata" not in preview.content
    original = await client.get(f"/api/versions/{vid}/receipts/{rid}?original=true")
    assert (
        original.content == receipt_image()
        and "attachment" in original.headers["content-disposition"]
    )
    other = (await client.post("/api/sessions", json={})).json()
    response = await client.post(
        "/api/chat", json={"session_id": other["session"]["id"], "message": "北京三日游"}
    )
    other_vid = response.json()["version_id"]
    assert (await client.get(f"/api/versions/{other_vid}/receipts/{rid}")).status_code == 400
    payload = {
        "title": "午餐",
        "amount": "86.35",
        "request_id": "receipt-expense-one",
        "receipt_ids": [rid],
    }
    wrong = await client.post(f"/api/versions/{other_vid}/expenses", json=payload)
    assert wrong.status_code == 400
    saved = await client.post(f"/api/versions/{vid}/expenses", json=payload)
    assert saved.status_code == 200, saved.text
    now = saved.json()["version"]["id"]
    retry = await client.post(f"/api/versions/{vid}/expenses", json=payload)
    assert retry.status_code == 200
    correction = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "午餐记错了，改成80元"}
    )
    now = correction.json()["version_id"]
    detail = (await client.get(f"/api/versions/{now}")).json()
    assert detail["itinerary"]["expenses"][0]["amount_cents"] == 8000
    assert detail["itinerary"]["expenses"][0]["receipt_ids"] == [rid]
    duplicate = await client.post(
        f"/api/versions/{now}/expenses", json={**payload, "request_id": "receipt-expense-two"}
    )
    assert duplicate.status_code == 409
    shared = await client.post(
        f"/api/versions/{now}/expenses",
        json={**payload, "request_id": "receipt-expense-two", "allow_shared_receipts": True},
    )
    assert shared.status_code == 200
    corrected = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "午餐记错了，改成80元"}
    )
    # 同名多笔时不猜测，原附件不丢失。
    detail = (await client.get(f"/api/versions/{corrected.json()['version_id']}")).json()
    assert detail["itinerary"]["expenses"][0]["receipt_ids"] == [rid]
    await client.delete(f"/api/sessions/{sid}")
    assert await store.get_receipt(sid, rid) is None


async def test_invalid_receipts_and_ocr_draft_never_record_money(environment, monkeypatch):
    client, _, _, _ = environment
    _, _, before = await generate(client)
    vid = before["version"]["id"]
    for raw in (
        b'<svg onload="alert(1)"></svg>',
        b"\x89PNG\r\n\x1a\nnot-a-real-image",
        b"x" * (4 * 1024 * 1024 + 1),
    ):
        response = await client.post(f"/api/versions/{vid}/receipts", content=raw)
        assert response.status_code == 400
    rid = await upload(client, vid)

    class Vision:
        async def structured(self, messages, schema, **kwargs):
            assert messages[-1].images[0].startswith("data:image/jpeg;base64,")
            return ReceiptDraft(
                title="午餐", amount="86.35", currency="CNY", kind="expense", category="meal"
            )

    monkeypatch.setattr("app.api.routes_itinerary.create_llm_provider", lambda settings: Vision())
    parsed = await client.post(f"/api/versions/{vid}/receipts/{rid}/recognise")
    assert parsed.status_code == 200 and parsed.json()["amount"] == "86.35"
    assert (await client.get(f"/api/versions/{vid}")).json() == before

    class Unsupported:
        async def structured(self, *args, **kwargs):
            raise LLMUnavailable("不支持图片")

    monkeypatch.setattr(
        "app.api.routes_itinerary.create_llm_provider", lambda settings: Unsupported()
    )
    failed = await client.post(f"/api/versions/{vid}/receipts/{rid}/recognise")
    assert failed.status_code == 400 and "手动填写" in failed.json()["error"]["message"]
    assert (await client.get(f"/api/versions/{vid}/receipts/{rid}")).status_code == 200


@pytest.mark.parametrize("currency,kind", [("other", "expense"), ("CNY", "refund")])
async def test_non_expense_or_foreign_receipts_do_not_prefill_money(currency, kind):
    class Vision:
        async def structured(self, *args, **kwargs):
            return ReceiptDraft(amount="86.35", currency=currency, kind=kind)

    draft = await recognise_receipt(Vision(), b"preview")
    assert draft.amount is None and draft.warnings
