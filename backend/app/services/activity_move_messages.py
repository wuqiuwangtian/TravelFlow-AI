"""把明确搬移请求变成两天草稿；同名地点先选真实活动。"""

import re
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import AppError, InvalidRequest
from app.domain.itinerary import Itinerary
from app.domain.linked_replan import (
    ActivityMove,
    LinkedReplanPreview,
    MoveChoice,
    MoveClarification,
)
from app.domain.models import Requirements
from app.services.journey_messages import itinerary_fingerprint
from app.services.linked_replan_messages import DAY_NUMBER, linked_input, linked_preview_reply
from app.services.linked_replan_service import preview_linked_replan
from app.services.travel_messages import _number
from app.services.travel_time import time_label

VERB = r"移到|挪到|移至|搬到|挪去|调整到"
PATTERN = re.compile(
    rf"^(?:请|帮我|能不能|可以)?(?:把|将)?第\s*({DAY_NUMBER})\s*天(?:的)?(.{{1,100}}?)"
    rf"(?:{VERB})(?:第\s*({DAY_NUMBER})\s*天|(次日|明天))"
    rf"(?:第\s*({DAY_NUMBER})\s*站(?:之)?前)?"
    r"((?:[，,；;]\s*(?:预留\d+(?:\.\d+)?小时休息|次日\d{1,2}:\d{2}出发))*)[。！!\s]*$"
)


@dataclass
class MoveReply:
    reply: str
    preview: LinkedReplanPreview | None = None
    pending: MoveClarification | None = None


async def handle_move_message(
    text: str,
    itinerary: Itinerary,
    requirements: Requirements,
    settings: Settings,
    pending: MoveClarification | None = None,
) -> MoveReply | None:
    selected = None
    if pending:
        answer = re.fullmatch(
            rf"(?:选|选择)?第\s*({DAY_NUMBER})\s*(?:个|项|笔)", text.strip().rstrip("。！! ")
        )
        if answer:
            if pending.itinerary_fingerprint != itinerary_fingerprint(itinerary):
                return MoveReply("行程已改变，请重新说明要搬移的景点。原计划未改变。")
            index = _number(answer[1]) - 1
            if not 0 <= index < len(pending.choices):
                return MoveReply("请选择列表中的地点，或取消这次操作。", pending=pending)
            selected = pending.choices[index].move
            text = pending.original_text
    requested = bool(re.search(VERB, text) and re.search(rf"第\s*({DAY_NUMBER})\s*天", text))
    if not requested:
        return None
    if re.search(r"如果|假如|假设", text) or re.match(
        r"^(?:请)?(?:不要|先别|别|不用)", text.strip()
    ):
        return MoveReply("景点跨天搬移会先预览两天变化，确认前不改计划和账本。")
    if re.search(r"记账|花了|付了|消费|退款|报销|已预约|预约记录", text):
        return MoveReply("本次没有保存任何操作。请将记账、回款或预约录入与景点搬移分别发送。")
    match = PATTERN.fullmatch(text.strip())
    if not match:
        return MoveReply(
            "请说明原日期、景点和相邻目标日，例如「把第1天公园移到第2天」；也可用「把第1天第3站移到第2天第2站前」指定顺序。原计划未改变。"
        )
    source = _number(match[1])
    destination = _number(match[3]) if match[3] else source + 1
    try:
        if abs(source - destination) != 1:
            raise InvalidRequest("景点搬移先支持相邻两天，请确认原日期和目标日期。")
        source_day = next((d for d in itinerary.days if d.day == source), None)
        destination_day = next((d for d in itinerary.days if d.day == destination), None)
        if source_day is None or destination_day is None:
            raise InvalidRequest("所选日期不在当前行程中。")
        selector = match[2].strip()
        ordinal = re.fullmatch(rf"第\s*({DAY_NUMBER})\s*站(?:「(.+)」)?", selector)
        if ordinal:
            index = _number(ordinal[1]) - 1
            candidates = source_day.activities[index : index + 1] if index >= 0 else []
            if ordinal[2] and candidates and candidates[0].title != ordinal[2]:
                raise InvalidRequest("该站名称已变化，请刷新后重新选择要搬移的地点。")
        else:
            exact = [
                a
                for a in source_day.activities
                if selector in {a.title, a.place_name, a.place.name if a.place else ""}
            ]
            candidates = exact or [
                a
                for a in source_day.activities
                if len(selector) >= 2 and (selector in a.title or selector in a.place_name)
            ]
        if not candidates:
            raise InvalidRequest("没有找到要搬移的具体地点，请说明原行程的地点名或第几站。")
        before_id = None
        if match[5]:
            position = _number(match[5]) - 1
            if not 0 <= position < len(destination_day.activities):
                raise InvalidRequest("目标停留点编号不在当天行程中，请重新确认顺序。")
            before_id = destination_day.activities[position].id
        data = linked_input(text, itinerary, allow_moves=True)
        if selected is None and len(candidates) > 1:
            choices = [
                MoveChoice(
                    move=ActivityMove(
                        activity_id=a.id,
                        source_day=source,
                        destination_day=destination,
                        before_activity_id=before_id,
                    ),
                    label=(
                        f"第{source}天第{source_day.activities.index(a) + 1}站 · {a.title} · "
                        f"{a.place_name} · "
                        f"{time_label(a.start_time or '时间待定', a.start_day_offset)}"
                    )
                    + (" · 已预约" if a.booking else ""),
                )
                for a in candidates
            ]
            prompt = (
                f"第{source}天有多个符合描述的地点，请选择要移到第{destination}天的那一站；"
                "选择后先预览，尚不保存。"
            )
            return MoveReply(
                prompt + "\n" + "\n".join(f"{i}. {c.label}" for i, c in enumerate(choices, 1)),
                pending=MoveClarification(
                    prompt=prompt,
                    choices=choices,
                    original_text=text,
                    itinerary_fingerprint=itinerary_fingerprint(itinerary),
                ),
            )
        move = selected or ActivityMove(
            activity_id=candidates[0].id,
            source_day=source,
            destination_day=destination,
            before_activity_id=before_id,
        )
        if move.activity_id not in {a.id for a in candidates}:
            raise InvalidRequest("所选地点已变化，请重新发起搬移。")
        data.moves = [move]
        preview = await preview_linked_replan(itinerary, data, requirements, settings)
        return MoveReply(linked_preview_reply(preview), preview=preview)
    except AppError as exc:
        return MoveReply(exc.message)
