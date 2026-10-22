export function timeLabel(time: string | null | undefined, offset = 0): string {
  if (!time) return "按安排";
  return `${offset === 0 ? "" : offset === 1 ? "次日 " : `${offset}天后 `}${time}`;
}

export function dayLabel(offset: number): string {
  return offset === 0 ? "当日" : offset === 1 ? "次日" : `${offset}天后`;
}
