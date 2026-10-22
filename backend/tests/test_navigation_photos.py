import httpx
import respx
from test_itinerary_api import generate

from app.core.config import Settings, get_settings
from app.maps.amap import safe_photos


def test_photo_sources_are_real_provider_urls_and_safe():
    assert safe_photos(
        [
            {"url": "http://store.is.autonavi.com/showpic/a.jpg"},
            {"url": "javascript:alert(1)"},
            {"url": "https://autonavi.com.evil.test/a"},
            {"url": "https://user:secret@store.is.autonavi.com/a"},
            {"url": "http://["},
        ]
    ) == ["https://store.is.autonavi.com/showpic/a.jpg"]


@respx.mock
async def test_gps_conversion_uses_gps_source_and_returns_gcj_for_navigation(environment):
    client, _, _, _ = environment
    app = client._transport.app
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, amap_api_key="test-map-key"
    )
    route = respx.get("https://restapi.amap.com/v3/assistant/coordinate/convert").mock(
        return_value=httpx.Response(200, json={"status": "1", "locations": "116.41,39.91"})
    )
    response = await client.post(
        "/api/maps/convert-location", json={"longitude": 116.4, "latitude": 39.9}
    )
    assert response.status_code == 200, response.text
    assert response.json()["longitude"] == 116.41
    assert route.calls[0].request.url.params["coordsys"] == "gps"
    assert route.calls[0].request.url.params["locations"] == "116.400000,39.900000"
    assert "test-map-key" not in response.text
    assert (
        await client.post("/api/maps/convert-location", json={"longitude": 181, "latitude": 0})
    ).status_code == 400


async def test_missing_map_key_and_photo_fallback_keep_trip_usable(environment):
    client, _, _, _ = environment
    _, _, before = await generate(client)
    vid = before["version"]["id"]
    aid = before["itinerary"]["days"][0]["activities"][0]["id"]
    response = await client.get(f"/api/versions/{vid}/activities/{aid}/photos")
    assert response.status_code == 200 and response.json() == {"photos": []}
    assert (
        await client.get(f"/api/versions/{vid}/activities/not-in-trip/photos")
    ).status_code == 400
    assert (
        await client.post("/api/maps/convert-location", json={"longitude": 116.4, "latitude": 39.9})
    ).status_code == 400
