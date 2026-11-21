"""Generate a synthetic cross-repo source database and its per-run expected values."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from browser_server import app
from httpx import ASGITransport, AsyncClient
from test_itinerary_api import generate
from test_receipts import upload
from test_travel_photos import payload, upload_photo


async def generate_sample(folder):
    folder.mkdir(mode=0o700)
    app.state.acceptance_database = folder / "travel.sqlite"
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as client,
    ):
        sid, bid, detail = await generate(client)
        first = detail["version"]["id"]
        receipt = await upload(client, first)
        saved = await client.post(
            f"/api/versions/{first}/expenses",
            json={
                "title": "虚构午餐",
                "amount": "86.35",
                "receipt_ids": [receipt],
                "request_id": "d1-fictional-lunch",
            },
        )
        saved.raise_for_status()
        second = saved.json()["version"]["id"]
        photo = await upload_photo(client, second, "blue")
        recap = await payload(
            client, second, [{"id": photo, "included": True, "caption": "蓝色样图"}], photo
        )
        saved = await client.put(f"/api/versions/{second}/recap", json=recap)
        saved.raise_for_status()
        head = saved.json()["version"]["id"]
        forked = await client.post(
            f"/api/sessions/{sid}/branches",
            json={
                "title": "虚构备选",
                "parent_branch_id": bid,
                "fork_version_id": first,
            },
        )
        forked.raise_for_status()
        fork = next(b["id"] for b in forked.json()["items"] if b["id"] != bid)
        versions = (await client.get(f"/api/branches/{fork}/versions")).json()["items"]
        files = {}
        for route in (
            f"receipts/{receipt}",
            f"receipts/{receipt}?original=true",
            f"travel-photos/{photo}",
        ):
            response = await client.get(f"/api/versions/{head}/{route}")
            response.raise_for_status()
            files[route] = hashlib.sha256(response.content).hexdigest()
        expected = {
            "session": sid,
            "title": "北京三日游",
            "main_branch": bid,
            "fork_branch": fork,
            "main_versions": [first, second, head],
            "fork_versions": [v["id"] for v in versions],
            "fork_from": first,
            "receipt": receipt,
            "photo": photo,
            "amount_cents": 8635,
            "attachments_sha256": files,
        }
    expected["database_sha256"] = hashlib.sha256(
        app.state.acceptance_database.read_bytes()
    ).hexdigest()
    (folder / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="A new directory with an existing parent")
    asyncio.run(generate_sample(parser.parse_args().directory.resolve()))
