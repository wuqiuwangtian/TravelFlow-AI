"""相邻两天的口语入口；明确的快捷请求不依赖模型。"""

import re
from datetime import datetime

from app.core.errors import InvalidRequest
from app.domain.itinerary import Itinerary
from app.domain.linked_replan import LinkedReplanInput, LinkedReplanPreview
from app.services.travel_messages import _number

DAY_NUMBER = r"\d+|[一二三四五六七八九十两]+"
APPLY_LINKED = {"应用两天调整", "应用联动方案"}


def linked_requested(text: str) -> bool:
    if re.search(r"联动|两天一起(?:调整|重排)|相邻两天", text):
        return True
    dates = re.findall(rf"第\s*({DAY_NUMBER})\s*天", text)
    return len(set(dates)) >= 2 and bool(re.search(r"一起调整|一起重排|顺延|跟着调整", text))


def simple_link_request(text: str) -> bool:
    return bool(
        re.fullmatch(
            rf"(?:请|帮我)?联动(?:调整|重排)?第\s*(?:{DAY_NUMBER})\s*天"
            rf"(?:和|与|、|至)第\s*(?:{DAY_NUMBER})\s*天"
            r"(?:[，,；;]\s*(?:预留\d+(?:\.\d+)?小时休息|次日\d{1,2}:\d{2}出发))*[。！!\s]*",
            text.strip(),
        )
    )


def linked_input(
    text: str, itinerary: Itinerary, scope_day: int | None = None, *, allow_moves: bool = False
) -> LinkedReplanInput:
    numbers = sorted({_number(m) for m in re.findall(rf"第\s*({DAY_NUMBER})\s*天", text)})
    if len(numbers) > 2 or len(numbers) == 2 and numbers[1] != numbers[0] + 1:
        raise InvalidRequest("目前只联动相邻两天，请说明例如「联动调整第1天和第2天」。")
    contexts = [d for d in itinerary.days if d.replan_context]
    first = numbers[0] if numbers else scope_day
    if first is None and contexts:
        first = max(
            contexts,
            key=lambda d: d.replan_context.updated_at if d.replan_context else datetime.min,
        ).day
    if first is None:
        raise InvalidRequest("请说明要联动的相邻两天，例如「联动调整第1天和第2天」。")
    if not 1 <= first <= 59:
        raise InvalidRequest("所选日期不在可联动的旅行范围内。")
    if not allow_moves and re.search(r"移到|挪到|换酒店|换一家|新增|增加景点|替换", text):
        raise InvalidRequest(
            "本阶段联动只调整两天的时间与可选停留；跨天搬移或更换地点请单独说明。原计划未改变。"
        )
    rest = re.search(
        r"(?:预留|休息)\s*([+-]?\d+(?:\.\d+)?|[零一二三四五六七八九十两]+|半)"
        r"\s*(?:个)?小时",
        text,
    )
    rest_minutes = 480
    if rest:
        value = rest[1]
        if len(value) > 8:
            raise InvalidRequest("休息时长请填写0到24小时。")
        hours = (
            0.5
            if value == "半"
            else _number(value)
            if re.fullmatch(r"[零一二三四五六七八九十两]+", value)
            else float(value)
        )
        rest_minutes = round(hours * 60)
    if not 0 <= rest_minutes <= 1440:
        raise InvalidRequest("休息时长请填写0到24小时；没有说明时先按8小时生成建议。")
    departure = None
    for match in re.finditer(
        rf"(?:第\s*({DAY_NUMBER})\s*天|次日|翌日|明天)\s*"
        r"(\d{1,2}:\d{2})[^，,；;。\n]{0,20}?(?:出发|启程)",
        text,
    ):
        if match[1] is None or _number(match[1]) == first + 1:
            hour, minute = map(int, match[2].split(":"))
            if hour > 23 or minute > 59:
                raise InvalidRequest("次日出发时刻请使用有效的24小时制，例如「次日10:00出发」。")
            new_departure = f"{hour:02d}:{minute:02d}"
            if departure and departure != new_departure:
                raise InvalidRequest("请确认一个次日出发时刻，再生成两天预览。")
            departure = new_departure
    return LinkedReplanInput(first_day=first, rest_minutes=rest_minutes, next_departure=departure)


def linked_preview_reply(preview: LinkedReplanPreview) -> str:
    lines = [preview.prompt]
    for day in sorted({c.day for c in preview.changes}):
        lines.append(f"第{day}天：")
        changes = [c for c in preview.changes if c.day == day]
        lines.extend(
            f"- {c.title}：{c.before} → {c.after}" + ("（固定项目保留）" if c.protected else "")
            for c in changes[:12]
        )
        if len(changes) > 12:
            lines.append("其余项目见下方预览。")
    lines.extend(preview.conflicts)
    lines.extend(preview.warnings)
    return "\n".join(lines)
