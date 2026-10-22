"""旅行日内的相对时刻；午夜之后保留日期，避免把凌晨误排到前一天。"""


def minutes(time: str, day_offset: int = 0) -> int:
    hour, minute = map(int, time.split(":"))
    return day_offset * 1440 + hour * 60 + minute


def time_label(time: str, day_offset: int = 0) -> str:
    prefix = "" if day_offset == 0 else "次日 " if day_offset == 1 else f"{day_offset}天后 "
    return prefix + time


def window_error(start: str, start_offset: int, end: str | None, end_offset: int) -> str | None:
    if end is None:
        return "请选择结束时间后再设置结束日期" if end_offset else None
    duration = minutes(end, end_offset) - minutes(start, start_offset)
    if duration <= 0:
        return "结束时刻需晚于出发时刻；跨午夜请明确选择次日"
    if duration > 1440:
        return "一次调整最多覆盖24小时，较长行程请分段调整"
    return None
