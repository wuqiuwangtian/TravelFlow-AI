import asyncio

import httpx

from app.agents.nodes.collector import CollectResult, DestinationIdea, DestinationOptions
from app.core.config import Settings
from app.core.errors import ProviderError
from app.domain.itinerary import Activity, DayDraft, DayPlan, Place, RouteLeg, ScheduledActivity
from app.domain.models import Requirements
from app.maps import assets
from app.maps.amap import AmapProvider
from app.maps.matching import choose_place
from app.services.itinerary_service import build_itinerary, check_day


def point(id, name, city="成都", longitude=104.14, latitude=30.63, **kwargs):
    return Place(
        id=id,
        name=name,
        longitude=longitude,
        latitude=latitude,
        city=city,
        citycode="028",
        **kwargs,
    )


def test_station_abbreviation_prefers_main_station_over_entrance():
    stop = ScheduledActivity(
        id="a", title="从成都东出发", place_name="成都东", city="成都", kind="station"
    )
    entrance = point("gate", "成都东站(进站口)", typecode="150200")
    main = point("station", "成都东站", typecode="150200")
    chosen, _ = choose_place(stop, [entrance, main])
    assert chosen.id == "station"


def test_scenic_name_does_not_resolve_to_the_county():
    stop = ScheduledActivity(
        id="a", title="游览九寨沟", place_name="九寨沟景区", city="阿坝", kind="attraction"
    )
    county = point("county", "九寨沟县", city="阿坝藏族羌族自治州", typecode="190100")
    scenic = point("scenic", "九寨沟国家级自然保护区", city="阿坝藏族羌族自治州", typecode="110202")
    chosen, _ = choose_place(stop, [county, scenic])
    assert chosen.id == "scenic"


def test_same_physical_poi_records_are_not_presented_as_ambiguity():
    stop = ScheduledActivity(
        id="a", title="成都东站", place_name="成都东站", city="成都", kind="station"
    )
    main = point("a", "成都东站", typecode="150200")
    duplicate = main.model_copy(update={"id": "b", "longitude": 104.1401})
    assert choose_place(stop, [main, duplicate])[0] is not None


def test_real_distinct_hotel_branches_need_context():
    stop = ScheduledActivity(id="a", title="酒店", place_name="同名酒店", city="成都", kind="hotel")
    first = point("a", "同名酒店", address="春熙路", typecode="100100")
    other = point("b", "同名酒店", longitude=104.25, address="机场路", typecode="100100")
    assert choose_place(stop, [first, other])[0] is None
    stop.address_hint = "春熙路"
    assert choose_place(stop, [first, other])[0].id == "a"


def test_large_scenic_area_records_share_one_destination():
    stop = ScheduledActivity(
        id="a", title="游览九寨沟", place_name="九寨沟景区", city="阿坝", kind="attraction"
    )
    first = point(
        "a",
        "九寨沟",
        city="阿坝藏族羌族自治州",
        district="九寨沟县",
        longitude=103.912874,
        latitude=33.142376,
        typecode="110201",
    )
    second = point(
        "b",
        "九寨沟风景区",
        city="阿坝藏族羌族自治州",
        district="九寨沟县",
        longitude=103.932775,
        latitude=33.230725,
        typecode="110202",
    )
    assert choose_place(stop, [first, second])[0] is not None
    second.district = "另一区"
    assert choose_place(stop, [first, second])[0] is None


def test_legacy_transport_is_an_incoming_edge_not_a_150_minute_stop():
    drafts = [
        DayDraft(
            day=1,
            activities=[
                Activity(title="成都东站出发", place_name="成都东站", city="成都"),
                Activity(
                    title="从成都乘高铁至黄龙九寨站",
                    place_name="黄龙九寨站",
                    city="阿坝",
                    duration_minutes=150,
                    cost=170,
                    cost_basis="per_person",
                    source_indices=[1],
                ),
                Activity(title="九寨沟", place_name="九寨沟", city="阿坝"),
                Activity(title="九寨沟", place_name="九寨沟", city="阿坝"),
            ],
        )
    ]
    day = build_itinerary(drafts, Requirements(duration_days=1)).days[0]
    assert len(day.activities) == 3
    arrival = day.activities[1]
    assert arrival.transport_mode == "rail"
    assert arrival.transport_minutes == 150
    assert arrival.duration_minutes == 20
    assert arrival.transport_cost == 170
    assert arrival.cost is None


async def test_rail_segment_is_not_sent_to_city_transit_api():
    class Maps:
        async def route(self, *args):
            raise AssertionError("铁路不能调用市内算路")

    first = ScheduledActivity(
        id="a",
        title="成都东站出发",
        place_name="成都东站",
        city="成都",
        kind="station",
        start_time="08:00",
        duration_minutes=20,
        place=point("a", "成都东站"),
    )
    second = ScheduledActivity(
        id="b",
        title="抵达黄龙九寨站",
        place_name="黄龙九寨站",
        city="阿坝",
        kind="station",
        duration_minutes=20,
        place=point("b", "黄龙九寨站", city="阿坝", longitude=103.7),
        transport_mode="rail",
        transport_minutes=150,
        transport_cost=170,
        transport_cost_basis="per_person",
        transport_source_indices=[1],
    )
    result = await check_day(
        DayPlan(day=1, activities=[first, second]), Requirements(travelers=2), Maps()
    )
    assert result.legs[0].mode == "rail"
    assert result.legs[0].status == "planned"
    assert result.known_cost == 340
    assert result.activities[1].start_time == "10:50"
    assert result.activities[1].time_is_suggested


async def test_auto_transit_failure_falls_back_to_road_transfer():
    class Maps:
        calls = []

        async def route(self, a, b, mode):
            self.calls.append(mode)
            if mode == "transit":
                raise ProviderError("暂无公交")
            return RouteLeg(
                from_id="",
                to_id="",
                mode="driving",
                status="ok",
                duration_seconds=1200,
                distance_meters=6000,
                walking_meters=0,
            )

    maps = Maps()
    first = ScheduledActivity(id="a", title="A", place=point("a", "A"))
    second = ScheduledActivity(
        id="b",
        title="B",
        place=point("b", "B", longitude=104.25),
        transport_cost=30,
        transport_cost_basis="total",
    )
    result = await check_day(
        DayPlan(day=1, activities=[first, second]), Requirements(travelers=2), maps
    )
    assert maps.calls == ["transit", "driving"]
    assert result.legs[0].mode == "driving"
    assert result.known_cost == 30
    assert result.estimated_cost == 30


async def test_fixed_user_time_is_not_silently_overwritten():
    first = ScheduledActivity(id="a", title="游览", start_time="09:00", duration_minutes=120)
    fixed = ScheduledActivity(
        id="b",
        title="已约活动",
        start_time="10:00",
        duration_minutes=60,
        time_is_suggested=False,
        transport_mode="transfer",
        transport_minutes=30,
    )
    result = await check_day(DayPlan(day=1, activities=[first, fixed]), Requirements(), None)
    assert result.activities[1].start_time == "10:00"
    assert any("冲突" in warning for warning in result.warnings)


async def test_static_map_retries_without_large_overlay_and_accepts_png_signature():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if len(seen) == 1:
            return httpx.Response(400)
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\nmap",
            headers={"content-type": "application/octet-stream"},
        )

    day = DayPlan(
        day=1,
        activities=[ScheduledActivity(id="a", title="站点", place=point("a", "站点"))],
        legs=[
            RouteLeg(
                from_id="a",
                to_id="b",
                mode="walking",
                status="ok",
                polyline=["104.14,30.63", "104.15,30.64"],
            )
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, mime = await AmapProvider(client, "fake-key").static_map(day)
    assert mime == "image/png"
    assert seen[0]["markers"].startswith("small,0x14b8a6,:")
    assert "paths" in seen[0]
    assert "paths" not in seen[1]


async def test_map_requests_for_one_version_are_combined_and_cached(monkeypatch):
    calls = []

    async def fake_map(self, day):
        calls.append(day.day)
        await asyncio.sleep(0)
        return b"image", "image/png"

    monkeypatch.setattr(AmapProvider, "static_map", fake_map)
    assets._cache.clear()
    day = DayPlan(day=1)
    settings = Settings(_env_file=None, amap_api_key="fake-map-key")
    result = await asyncio.gather(
        *(assets.map_asset("cache-test", day, settings) for _ in range(3))
    )
    assert len(result) == 3
    assert calls == [1]


async def test_destination_inspiration_then_city_choice(environment, monkeypatch):
    from conftest import FakeLLM

    client, _, _, search = environment
    original = FakeLLM.structured

    async def structured(self, messages, schema, **kwargs):
        if schema is CollectResult:
            if messages[-1].content == "第二个":
                assert "重庆" in messages[0].content
                return CollectResult(extracted=Requirements(destination="重庆"), reply="就去重庆")
            return CollectResult(
                extracted=Requirements(origin="上海", duration_days=3, budget=2000),
                reply="先选城市",
                wants_destination_ideas=True,
            )
        if schema is DestinationOptions:
            return DestinationOptions(
                ideas=[
                    DestinationIdea(city="成都", reason="轻松美食"),
                    DestinationIdea(city="重庆", reason="山城夜景"),
                ]
            )
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    session = (await client.post("/api/sessions", json={})).json()
    sid = session["session"]["id"]
    first = (
        await client.post("/api/chat", json={"session_id": sid, "message": "不知道去哪"})
    ).json()
    assert "重庆" in first["reply"]
    assert first["version_id"] is None
    second = await client.post("/api/chat", json={"session_id": sid, "message": "第二个"})
    assert second.status_code == 200
    assert second.json()["version_id"]
    assert len(search.queries) == 4


async def test_generic_hotel_region_becomes_a_real_recommendation():
    from app.services.itinerary_service import _recommend_concrete_place

    class Maps:
        async def search_places(self, keyword, city):
            assert keyword == "漳扎镇 酒店"
            return [
                point(
                    "hotel",
                    "真实酒店",
                    city="阿坝",
                    district="九寨沟县",
                    address="漳扎镇街道12号",
                    typecode="100100",
                )
            ]

    stop = ScheduledActivity(id="a", title="入住", kind="hotel", place_name="漳扎镇沟口住宿区")
    assert await _recommend_concrete_place(stop, Maps(), "阿坝")
    assert stop.place_name == "真实酒店"
    assert stop.place_status == "matched"
    assert "建议" in stop.notes
    # 已指定具体酒店时，不以通用推荐覆盖用户的选择。
    assert not await _recommend_concrete_place(stop, Maps(), "阿坝")


async def test_slow_map_service_keeps_places_already_resolved(monkeypatch):
    from app.domain.itinerary import Itinerary
    from app.services.itinerary_service import enrich_itinerary

    class SlowMaps:
        def __init__(self, *args):
            self.calls = 0

        async def search_places(self, keyword, city):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError()
            return [point("a", "成都东站", typecode="150200")]

    monkeypatch.setattr("app.services.itinerary_service.AmapProvider", SlowMaps)
    source = Itinerary(
        days=[
            DayPlan(
                day=1,
                activities=[
                    ScheduledActivity(
                        id="a", title="成都东站", place_name="成都东站", city="成都", kind="station"
                    ),
                    ScheduledActivity(id="b", title="第二站", place_name="第二站", city="成都"),
                ],
            )
        ]
    )
    result = await enrich_itinerary(
        source, Requirements(), Settings(_env_file=None, amap_api_key="fake")
    )
    assert result.days[0].activities[0].place.id == "a"
    assert source.days[0].activities[0].place is None
    assert "较慢" in result.days[0].warnings[0]
