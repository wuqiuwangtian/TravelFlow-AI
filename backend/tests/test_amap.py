import httpx
import pytest

from app.core.errors import ProviderError
from app.domain.itinerary import DayPlan, Place, ScheduledActivity
from app.maps.amap import AmapProvider

KEY = "fake-development-key"
POINT = {"id": "poi-a", "name": "故宫博物院", "location": "116.397,39.918", "citycode": "010"}


async def test_search_and_detail_use_verified_coordinates():
    def handler(request):
        assert request.url.params["key"] == KEY
        if request.url.path.endswith("/text"):
            assert request.url.params["city_limit"] == "false"
        return httpx.Response(200, json={"status": "1", "pois": [POINT]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        maps = AmapProvider(client, KEY)
        places = await maps.search_places("故宫博物院", "北京")
        assert places[0].longitude == 116.397
        assert (await maps.get_place("poi-a")).id == "poi-a"


@pytest.mark.parametrize("mode", ["walking", "driving", "transit"])
async def test_route_response_contract(mode):
    def handler(request):
        if mode == "transit":
            route = {
                "transits": [
                    {"distance": "3000", "duration": "1500", "walking_distance": "800", "cost": "4"}
                ]
            }
        else:
            assert request.url.params["show_fields"] == "cost,polyline"
            route = {
                "paths": [
                    {
                        "distance": "3000",
                        "cost": {"duration": "1500"},
                        "steps": [{"polyline": "116.397,39.918;116.4,39.92"}],
                    }
                ]
            }
        return httpx.Response(200, json={"status": "1", "route": route})

    point = Place(id="a", name="a", longitude=116.397, latitude=39.918, citycode="010")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        route = await AmapProvider(client, KEY).route(point, point, mode)
    assert route.duration_seconds == 1500
    assert route.distance_meters == 3000
    assert route.walking_meters == (3000 if mode == "walking" else 800 if mode == "transit" else 0)
    assert route.cost == (4 if mode == "transit" else 0 if mode == "walking" else None)


@pytest.mark.parametrize("failure", ["http", "json", "status"])
async def test_provider_errors_never_echo_key(failure):
    def handler(request):
        if failure == "http":
            return httpx.Response(403)
        if failure == "json":
            return httpx.Response(200, text="not json")
        return httpx.Response(200, json={"status": "0", "info": KEY})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await AmapProvider(client, KEY).search_places("故宫", "北京")
    assert KEY not in str(caught.value)
    assert caught.value.__suppress_context__


async def test_static_map_proxies_image_without_returning_credential_url():
    def handler(request):
        assert request.url.params["key"] == KEY
        assert "markers" in request.url.params
        return httpx.Response(
            200, content=b"\x89PNG\r\n\x1a\ntest-image", headers={"content-type": "image/png"}
        )

    day = DayPlan(
        day=1,
        activities=[
            ScheduledActivity(
                id="a",
                title="故宫",
                place=Place(
                    **{
                        "id": "a",
                        "name": "故宫",
                        "longitude": 116.397,
                        "latitude": 39.918,
                    }
                ),
            )
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content, mime = await AmapProvider(client, KEY).static_map(day)
    assert content == b"\x89PNG\r\n\x1a\ntest-image"
    assert mime == "image/png"
