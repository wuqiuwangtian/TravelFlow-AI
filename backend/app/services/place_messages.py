"""用真实候选承接地点澄清；明确选择后才改变行程，歧义不猜测。"""

import re
import unicodedata
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.core.errors import InvalidRequest, ProviderError
from app.domain.clarification import PlaceClarification, PlaceTarget
from app.domain.itinerary import Itinerary, Place, ScheduledActivity
from app.domain.models import Requirements
from app.maps.amap import AmapProvider
from app.maps.matching import choose_place
from app.services.itinerary_service import enrich_itinerary
from app.services.journey_messages import itinerary_fingerprint
from app.services.replan_service import explicit_day
from app.services.travel_messages import _number

NUM = r"(?:\d+|[一二三四五六七八九十两]+)"
ORDINAL = re.compile(rf"第\s*({NUM})\s*站")
NOT_CHOICE = re.compile(
    r"如果|假如|不要|不用|不是|别选|别改|不改|能不能|能否|是否|吗|[?？]|"
    r"记账|花了|付了|消费|退款|报销|预约|已完成|逛完|正在|轻松|少走|重排"
    r"|预算|预估|费用|金额|实付|总额|\d\s*(?:元|块|RMB|CNY)"
)
REQUEST = re.compile(
    r"(?:确认|确定|澄清|核对|选|更正).*(?:地点|地址)|定位|哪家|哪一家|(?:地点|地址).*(?:不对|歧义|是|在)"
)
REPLACE = re.compile(r"(?:换成|换到|改成|改为|改到)\s*(.+?)\s*[。！!]*$")


@dataclass
class PlaceReply:
    reply: str
    itinerary: Itinerary | None = None
    pending: PlaceClarification | None = None


def _compact(text: str) -> str:
    return re.sub(r"[\s，,。:：、()（）·\-]", "", unicodedata.normalize("NFKC", text)).lower()


def _targets(text: str, itinerary: Itinerary) -> list[PlaceTarget]:
    day = explicit_day(text)
    ordinal = ORDINAL.search(text)
    result = []
    for d in itinerary.days:
        if day is not None and d.day != day:
            continue
        for i, a in enumerate(d.activities, 1):
            named = any(name and name in text for name in (a.title, a.place_name))
            if (ordinal and i == _number(ordinal[1])) or (not ordinal and named):
                result.append(PlaceTarget(day=d.day, ordinal=i, activity_id=a.id, title=a.title))
    return result


def _activity(itinerary: Itinerary, target: PlaceTarget) -> ScheduledActivity:
    return next(
        a
        for d in itinerary.days
        if d.day == target.day
        for a in d.activities
        if a.id == target.activity_id
    )


def _prompt(pending: PlaceClarification, itinerary: Itinerary, note: str = "") -> PlaceReply:
    if pending.activity_id:
        title = next(
            a.title for d in itinerary.days for a in d.activities if a.id == pending.activity_id
        )
        heading = f"正在确认第{pending.day}天「{title}」的具体地点。"
        if pending.candidates:
            lines = [
                f"{i}. {p.name} · {p.city}{p.district} {p.address}"
                for i, p in enumerate(pending.candidates, 1)
            ]
            question = "\n".join(lines) + "\n可以回复「选第二个」，或补充区域、街道与门牌号。"
        else:
            question = "可说「换成具体餐厅名」，或补充区域、地址，例如「地址是朝阳区望京路1号」。"
    else:
        heading = (
            f"将地点改为「{pending.query_name}」前，先确认是哪一站。"
            if pending.replacement
            else "先确认要核对哪一站的地点。"
        )
        question = "\n".join(f"第{t.day}天第{t.ordinal}站 · {t.title}" for t in pending.targets)
        question += "\n可以直接回复「第1天第2站」。"
    pending.prompt = "\n".join(
        filter(None, [note, heading, question, "地点尚未更改，也可以说「取消这次操作」。"])
    )
    return PlaceReply(pending.prompt, pending=pending)


async def _apply(
    selected: Place,
    pending: PlaceClarification,
    itinerary: Itinerary,
    requirements: Requirements,
    settings: Settings,
) -> PlaceReply:
    working = itinerary.model_copy(deep=True)
    activity = next(a for d in working.days for a in d.activities if a.id == pending.activity_id)
    if activity.booking or activity.progress != "not_started" or activity.is_context_anchor:
        return PlaceReply(
            "此站已有预约或旅中记录，地点没有改变。请先核对或更正已有记录，再修改地点。"
        )
    activity.place = selected.model_copy(deep=True)
    activity.place_name = selected.name
    activity.city = selected.city or activity.city
    activity.address_hint = selected.address[:200]
    activity.place_status = "matched"
    activity.candidates = [p.model_copy(deep=True) for p in pending.candidates]
    activity.match_reason = "已按你的选择确认具体地点。"
    activity.source_indices = []
    if pending.replacement:
        activity.title = selected.name[:100]
        activity.cost, activity.cost_basis, activity.cost_source = None, "unknown", "unknown"
        activity.transport_minutes, activity.transport_cost = None, None
        activity.transport_cost_basis, activity.transport_source_indices = "unknown", []
        activity.notes = "已按你的要求更换地点，未核实的新费用保留未知。"
    elif activity.cost is not None and activity.cost_source != "manual":
        activity.cost_source = "estimate"
        activity.notes = (activity.notes + "；地点已确认，费用沿用原估算，实际价格待核实。")[-2000:]
    updated = await enrich_itinerary(working, requirements, settings, day_number=pending.day)
    if not any(a.id == pending.activity_id for d in updated.days for a in d.activities):
        return _prompt(
            pending,
            itinerary,
            "这个地点暂时排不进当前时间窗口，原行程已保留。请先调整剩余时间或路线。",
        )
    reply = (
        f"已确认第{pending.day}天的地点为「{selected.name}」"
        f"（{selected.city}{selected.district} {selected.address}），"
        "已更新当天路线、建议时间和费用汇总。"
    )
    day = next(d for d in updated.days if d.day == pending.day)
    if any("冲突" in w for w in day.warnings):
        reply += "仍有时间冲突，已有预约保持，详情见时间轴。"
    if any(leg.status not in {"ok", "planned"} for leg in day.legs):
        reply += "部分交通尚未核实，已保留建议安排。"
    return PlaceReply(reply, itinerary=updated)


async def _search(pending: PlaceClarification, hint: str, settings: Settings) -> list[Place]:
    if not settings.amap_api_key:
        raise InvalidRequest(
            "请先在「设置 → 高德地图」配置地图服务，再重试这条消息。原行程已保留。"
        )
    query = " ".join(dict.fromkeys(filter(None, [pending.query_name, hint])))
    async with httpx.AsyncClient(timeout=min(settings.amap_timeout_seconds, 8)) as client:
        try:
            places = await AmapProvider(client, settings.amap_api_key).search_places(
                query, pending.city
            )
        except (ProviderError, httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderError("地点查询暂时不可用，原行程已保留，请重试这条消息。") from None
    # 候选顺序是本轮编号的依据；同一个 POI 不展示两次。
    return list({p.id: p for p in places}.values())[:8]


async def handle_place_message(
    text: str,
    itinerary: Itinerary,
    requirements: Requirements,
    settings: Settings,
    pending: PlaceClarification | None = None,
) -> PlaceReply | None:
    text = text.strip()
    if NOT_CHOICE.search(text) and text.rstrip("。！!") not in {"都不是", "都不对", "没有我要的"}:
        return None
    if pending and pending.itinerary_fingerprint != itinerary_fingerprint(itinerary):
        return PlaceReply("行程已发生变化，原地点选择已失效。请重新说明要确认哪一天、哪一站。")
    if pending:
        pending = pending.model_copy(deep=True)
    if len(ORDINAL.findall(text)) > 1 or len(re.findall(rf"第\s*{NUM}\s*天", text)) > 1:
        return None
    replacement = REPLACE.search(text)
    if replacement and re.search(
        r"室内活动|户外活动|素食|清淡|便宜|\d\s*(?:点|分钟|小时)|\d[:：]\d", replacement[1]
    ):
        return None
    targets = _targets(text, itinerary)
    requested = bool(REQUEST.search(text) or replacement and targets)
    if not pending and not requested:
        return None
    # 待选地点时，一句完整的另一站请求切换到新目标，不能把编号错用到旧地点。
    if pending and pending.activity_id and requested and targets:
        pending = None
    if pending and pending.activity_id and replacement and not targets:
        target = next(
            PlaceTarget(day=d.day, ordinal=i, activity_id=a.id, title=a.title)
            for d in itinerary.days
            for i, a in enumerate(d.activities, 1)
            if a.id == pending.activity_id
        )
        return await handle_place_message(
            f"把第{target.day}天第{target.ordinal}站换成{replacement[1]}",
            itinerary,
            requirements,
            settings,
        )
    if (
        pending
        and not pending.activity_id
        and not re.fullmatch(rf"(?:是|选)?\s*第\s*{NUM}\s*天\s*第\s*{NUM}\s*站[。!！]*", text)
    ):
        return None
    if pending is None or not pending.activity_id:
        if not targets and not ORDINAL.search(text) and explicit_day(text) is None:
            targets = [
                PlaceTarget(day=d.day, ordinal=i, activity_id=a.id, title=a.title)
                for d in itinerary.days
                for i, a in enumerate(d.activities, 1)
                if a.place is None
                and a.kind != "free_time"
                and not a.booking
                and a.progress == "not_started"
            ]
        if len(targets) != 1:
            if not targets:
                return PlaceReply(
                    "没有找到这一天的这一个站点，请说明「第几天第几站」，原行程没有改变。"
                )
            draft = PlaceClarification(
                targets=targets[:12],
                query_name=replacement[1].strip(" 。！!") if replacement else "",
                replacement=bool(replacement),
                itinerary_fingerprint=itinerary_fingerprint(itinerary),
            )
            return _prompt(draft, itinerary)
        target = targets[0]
        activity = _activity(itinerary, target)
        if activity.booking or activity.progress != "not_started" or activity.is_context_anchor:
            return PlaceReply(
                "此站已有预约或旅中记录，地点没有改变。请先核对或更正已有记录，再修改地点。"
            )
        is_replacement = bool(replacement or pending and pending.replacement)
        query = (
            replacement[1].strip(" 。！!")
            if replacement
            else (
                pending.query_name
                if pending and pending.replacement
                else activity.place_name or activity.title
            )
        )
        if not query or len(query) > 100:
            return PlaceReply("请给出100字以内的具体地点名称，原行程没有改变。")
        pending = PlaceClarification(
            day=target.day,
            activity_id=target.activity_id,
            query_name=query,
            city=activity.city or requirements.destination or "",
            replacement=is_replacement,
            candidates=[] if is_replacement else activity.candidates[:8],
            itinerary_fingerprint=itinerary_fingerprint(itinerary),
        )
        # 已有明确地点的核对可以直接回答，不制造新的待选择步骤。
        if activity.place and not is_replacement:
            p = activity.place
            return PlaceReply(
                f"第{target.day}天「{activity.title}」已定位为「{p.name}」"
                f"（{p.city}{p.district} {p.address}），地点没有改变。"
            )
        if not pending.candidates:
            pending.candidates = await _search(pending, "", settings)
        stated_hint = re.search(r"(?:地点|地址)(?:是|在)\s*(.+)$", text)
        if stated_hint:
            resolved = await handle_place_message(
                stated_hint[1], itinerary, requirements, settings, pending
            )
            return resolved or _prompt(pending, itinerary)
        if len(pending.candidates) == 1:
            context = activity.model_copy(update={"place_name": query, "city": pending.city})
            chosen, _ = choose_place(context, pending.candidates)
            if chosen:
                return await _apply(chosen, pending, itinerary, requirements, settings)
        return _prompt(pending, itinerary)

    ordinal = re.fullmatch(
        rf"(?:就|选|选择|用|是)?\s*(?:第|候选)?\s*({NUM})\s*(?:个|家|项|号|个地点)?(?:吧)?[。！!]*",
        text,
    )
    if ordinal:
        index = _number(ordinal[1]) - 1
        if not 0 <= index < len(pending.candidates):
            return _prompt(pending, itinerary, "这个编号不在当前候选中，请按下面的候选重新选择。")
        return await _apply(pending.candidates[index], pending, itinerary, requirements, settings)
    if text.rstrip("。！!") in {"都不是", "都不对", "没有我要的"}:
        pending = pending.model_copy(deep=True)
        pending.candidates = []
        return _prompt(pending, itinerary)
    retry = text.rstrip("。！!") in {"重新搜索", "重试地点查询", "再查一次"}
    named = any(_compact(text) in _compact(p.name) for p in pending.candidates)
    if not retry and (
        explicit_day(text) is not None
        or re.search(r"想去|想玩|推荐|调整|安排|明天|后天", text)
        or not named
        and not re.search(
            r"区|县|镇|市|街|路|巷|号|店|馆|站|酒店|餐厅|公园|广场|地址|那家|那一家", text
        )
    ):
        return None
    hint = re.sub(r"^(?:我说的是|地址是|地点是|选|选择|是在|是|在)\s*", "", text)
    hint = re.sub(r"(?:的)?(?:那一家|那家|这家|那个|这个)(?:吧)?[。！!]*$", "", hint).strip(
        " 。！!"
    )
    compact = _compact(hint)
    matches = [
        p
        for p in pending.candidates
        if compact and compact in _compact(p.name + p.city + p.district + p.address)
    ]
    if len(matches) == 1:
        return await _apply(matches[0], pending, itinerary, requirements, settings)
    if len(matches) > 1:
        return _prompt(pending, itinerary, "这个区域仍有多个候选，请再补充街道、门牌或候选编号。")
    draft = pending.model_copy(deep=True)
    draft.candidates = await _search(draft, "" if retry else hint, settings)
    if (
        len(draft.candidates) == 1
        and compact
        and compact
        in _compact(
            draft.candidates[0].name
            + draft.candidates[0].city
            + draft.candidates[0].district
            + draft.candidates[0].address
        )
    ):
        return await _apply(draft.candidates[0], draft, itinerary, requirements, settings)
    return _prompt(
        draft,
        itinerary,
        "暂未找到对应地点。" if not draft.candidates else "已按补充信息重新查询地点。",
    )
