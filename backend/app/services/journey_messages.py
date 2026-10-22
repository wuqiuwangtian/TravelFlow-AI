"""按顺序校验一轮旅中操作；草稿全部成功后才交给节点统一保存。"""

import re
from dataclasses import dataclass, field

from app.core.errors import AppError
from app.domain.itinerary import Itinerary
from app.domain.models import Requirements
from app.services.adjustment_messages import handle_adjustment_message
from app.services.booking_messages import handle_booking_message
from app.services.replan_service import explicit_day
from app.services.travel_messages import TravelReply, handle_travel_message

_RECORD = re.compile(
    r"记账|记一笔|花了|付了|支付了|消费了|支出|记错|实付|消费.*(?:改成|改为|删除|移除)|"
    r"退款|退了|退回|报销|已预约|已经预约|预约好了|已经订好|已订好|记录已有预约|"
    r"(?:清除|取消|删除)预约记录|恢复未开始|跳过|不去这站|"
    r"已完成|完成了|标记完成|逛完|游览完|游完|正在游览|正在逛|开始这一站|开始游览"
)
_REFINEMENT = re.compile(r"调整|安排|重排|替换|换成|换一家|轻松|少走|规划")
_DAY_ONLY = re.compile(r"第\s*(?:\d+|[一二三四五六七八九十两]+)\s*天")
_NOT_ACTUAL = re.compile(r"如果|假如|预计|预算|估计|打算|计划|明天|能不能|能否|不要|是否|吗|[?？]")


@dataclass
class JourneyMessage:
    itinerary: Itinerary | None = None
    replies: list[str] = field(default_factory=list)
    refinement: str = ""
    booking_days: set[int] = field(default_factory=set)

    @property
    def reply(self) -> str:
        return "\n".join(self.replies)


def _clauses(text: str) -> list[str]:
    # 千分位不作为操作分隔符，小数点保留。
    text = re.sub(
        r"(?<!\d)(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?!\d)",
        lambda match: match[1].replace(",", ""),
        text,
    )
    parts = re.split(r"[，,；;。\n]+", text)
    result: list[str] = []
    for part in parts:
        # 「咖啡和甜点花了30元」仍是一笔，「午餐花了30元并且地铁花了5元」分两笔。
        pieces = re.split(r"(并且|然后|接着|同时|以及|并|和|再)", part)
        accumulated = pieces[0]
        for offset in range(1, len(pieces), 2):
            rest = "".join(pieces[offset + 1 :])
            if _RECORD.search(accumulated) and (_RECORD.search(rest) or _REFINEMENT.search(rest)):
                result.append(accumulated.strip())
                accumulated = pieces[offset + 1]
            else:
                accumulated += pieces[offset] + pieces[offset + 1]
        if accumulated.strip():
            result.append(accumulated.strip())
    joined: list[str] = []
    for part in result:
        if joined and (
            _DAY_ONLY.fullmatch(joined[-1])
            or re.match(r"改成|改为", part)
            and re.search(r"记错|更正|消费|账单", joined[-1])
            or re.fullmatch(r"第[\d一二三四五六七八九十两]+天第[\d一二三四五六七八九十两]+站", part)
            and re.search(r"(?:清除|取消|删除)预约记录", joined[-1])
            or re.fullmatch(r"(?:停留|时长|持续)\s*\d+\s*分钟", part)
            and re.search(r"预约|订好", joined[-1])
        ):
            joined[-1] += "，" + part
        else:
            joined.append(part)
    return joined


def prepare_journey_message(
    text: str, itinerary: Itinerary, requirements: Requirements
) -> JourneyMessage | None:
    clauses = _clauses(text)

    def asserted(part: str) -> bool:
        # 预约是已经发生的记录，即使票面日期在明天；未来预算仍不落账。
        probe = (
            re.sub("明天", "次日", part)
            if re.search(r"已预约|已经预约|预约好了|已经订好|已订好|记录已有预约", part)
            else part
        )
        return bool(_RECORD.search(part) and not _NOT_ACTUAL.search(probe))

    if not any(asserted(part) for part in clauses):
        return None
    multiple = len(clauses) > 1

    def fail(index: int, reason: str) -> JourneyMessage:
        prefix = f"本次没有保存任何操作，第{index + 1}项需要确认：" if multiple else ""
        return JourneyMessage(replies=[prefix + reason])

    if len(clauses) > 12:
        return fail(0, "一次最多处理12项，请缩短这条消息。")
    working = itinerary.model_copy(deep=True)
    result = JourneyMessage(itinerary=working)
    day: int | None = None
    for index, part in enumerate(clauses):
        part = re.sub(r"^(?:并且|然后|接着|同时|另外|再|也|还|并)\s*", "", part)
        explicit = explicit_day(part)
        if explicit is not None:
            day = explicit
        scoped = f"第{day}天，{part}" if day is not None and explicit is None else part
        if not _RECORD.search(part):
            tail = "，".join(clauses[index:])
            if (
                _REFINEMENT.search(tail)
                and not any(_RECORD.search(p) for p in clauses[index:])
                and result.replies
            ):
                result.refinement = (
                    f"第{day}天，{tail}" if day and explicit_day(tail) is None else tail
                )
                return result
            return fail(index, "这部分还不能明确执行，请说明具体要记录或调整的内容。")
        # 不把同一片段中未分清的多个站点或日期偷偷当作第一站处理。
        if (
            len(re.findall(r"第\s*[\d一二三四五六七八九十两]+\s*天", part)) > 1
            or len(re.findall(r"第\s*[\d一二三四五六七八九十两]+\s*站", part)) > 1
            or _REFINEMENT.search(part)
        ):
            return fail(index, "请用逗号或分号分隔每项操作，并分别说明对应日期、站点。")
        try:
            booking = handle_booking_message(scoped, working)
            action: TravelReply | None = (
                booking
                or handle_adjustment_message(scoped, working)
                or handle_travel_message(scoped, working, requirements)
            )
        except AppError as exc:
            return fail(index, exc.message)
        if action is None:
            if not multiple:
                return None  # 单条疑问、预算或假设仍交给原有意图流程。
            return fail(index, "请明确已发生的事实和要执行的操作；疑问、否定或假设不直接落记录。")
        if action.itinerary is None:
            return fail(index, action.reply)
        if booking:
            result.booking_days.update(
                d.day
                for d in action.itinerary.days
                if d != next(old for old in working.days if old.day == d.day)
            )
        working = action.itinerary
        result.itinerary = working
        result.replies.append(action.reply)
    return result
