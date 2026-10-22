"""结合名称、地点类型和城市选择主地点；真正不同的同名分店保留歧义。"""

from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher

from app.domain.itinerary import Place, ScheduledActivity


def normal_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[\s·・,，。:：\-—_]", "", value)
    return value.replace("火车站", "站").replace("高铁站", "站").replace("铁路车站", "站")


def distance_meters(a: Place, b: Place) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    angle = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(math.radians(b.longitude - a.longitude) / 2) ** 2
    )
    return 12742000 * math.asin(min(1, math.sqrt(angle)))


def scenic_core(value: str) -> str:
    return re.sub(
        r"(?:国家级自然保护区|国家公园|风景名胜区|旅游景区|风景区|景区|公园)$",
        "",
        normal_name(value),
    )


def _same_destination(activity: ScheduledActivity, first: Place, second: Place) -> bool:
    distance = distance_meters(first, second)
    if normal_name(first.name) == normal_name(second.name) and distance < 250:
        return True
    # 大型景区可能同时收录整体保护区和景区主体，不能把山谷中心与景区坐标当成分店。
    return (
        activity.kind == "attraction"
        and first.typecode.startswith("11")
        and second.typecode.startswith("11")
        and len(scenic_core(first.name)) >= 2
        and scenic_core(first.name) == scenic_core(second.name)
        and bool(first.district)
        and first.district == second.district
        and first.city == second.city
        and distance < 15000
    )


def _score(activity: ScheduledActivity, place: Place) -> float:
    query, name = normal_name(activity.place_name), normal_name(place.name)
    if not query:
        return 0
    aliases = {query}
    if activity.kind == "station" or re.search(r"[东南西北]$", query):
        aliases.add(query.removesuffix("站") + "站")
    if activity.kind == "attraction":
        for suffix in ("风景名胜区", "风景区", "景区", "公园", "博物院"):
            aliases.add(query + suffix)
    score = (
        0.88
        if name in aliases
        else (
            0.72
            if query in name or (len(name) >= 2 and name in query)
            else 0.65 * SequenceMatcher(None, query, name).ratio()
        )
    )
    if (
        activity.kind == "attraction"
        and len(scenic_core(query)) >= 2
        and scenic_core(query) == scenic_core(name)
    ):
        score = max(score, 0.9)
    city = activity.city.removesuffix("市")
    if city and any(
        city in value for value in (place.city, place.district, place.address, place.name)
    ):
        score += 0.08
    elif city and place.city and place.city.removesuffix("市") not in city:
        score -= 0.18
    type_prefix = {
        "station": ("1502", "1501"),
        "attraction": ("11",),
        "hotel": ("10",),
        "meal": ("05",),
    }.get(activity.kind, ())
    if type_prefix and place.typecode.startswith(type_prefix):
        score += 0.08
    # 优先景区/车站主体；除非用户本身指定入口或停车场，避免匹配到附属设施。
    for child in (
        "停车场",
        "售票处",
        "候车室",
        "候车厅",
        "进站口",
        "出站口",
        "卫生间",
        "出入口",
        "检票口",
        "游客中心",
        "入口",
        "出口",
    ):
        if child in place.name and child not in activity.place_name:
            score -= 0.24
            break
    if activity.address_hint and activity.address_hint in place.address:
        score += 0.18
    if place.typecode.startswith("19") and activity.kind in ("attraction", "station"):
        score -= 0.3
    return score


def choose_place(activity: ScheduledActivity, candidates: list[Place]) -> tuple[Place | None, str]:
    ranked = sorted(
        ((_score(activity, place), place) for place in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.82:
        return None, "地点信息不足，保留原名称"
    score, best = ranked[0]
    competitors = [entry for entry in ranked[1:] if not _same_destination(activity, entry[1], best)]
    if competitors and score - competitors[0][0] < 0.07:
        return None, "存在不同位置的同名地点"
    return best, "已根据城市、名称及地点类型自动定位"
