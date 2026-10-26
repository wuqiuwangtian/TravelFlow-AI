"""保持入选集合和容量约束，用有限交换减少同日景点分散度，不冒充实际路线。"""

from app.domain.studio_candidates import (
    CandidatePlace,
    CandidatePool,
    GeographicGroup,
    GeographyReport,
)
from app.maps.matching import distance_meters
from app.services.studio_location import fresh_locations, same_city


def group_nearby(
    buckets: list[list[CandidatePlace]],
    pool: CandidatePool,
    minutes: int,
    buffer: int,
    default_stay: int,
) -> tuple[list[list[CandidatePlace]], GeographyReport]:
    selected = [p for bucket in buckets for p in bucket]
    if not pool.location or not fresh_locations(pool.location.checked_at):
        return buckets, GeographyReport(
            reason="尚无24小时内的候选定位，沿用时长平衡；请先运行候选定位节点。"
        )
    missing = [
        p.place_name
        for p in selected
        if not p.location
        or p.location.status != "matched"
        or not p.location.place
        or not same_city(p.city, p.location.place.city)
    ]
    if missing:
        return buckets, GeographyReport(
            reason="部分入选地点未明确定位，沿用完整时长方案：" + "、".join(missing[:4])
        )
    points = {p.id: p.location.place for p in selected if p.location and p.location.place}
    distances = {
        (p.id, q.id): distance_meters(points[p.id], points[q.id])
        for p in selected
        for q in selected
    }

    def spread(groups: list[list[CandidatePlace]]) -> float:
        return sum(
            distances[p.id, q.id]
            for group in groups
            for i, p in enumerate(group)
            for q in group[i + 1 :]
        )

    def stay(p: CandidatePlace) -> int:
        return p.duration_minutes or default_stay

    result = [bucket[:] for bucket in buckets]
    before = spread(result)
    loads = [sum(stay(p) for p in b) + buffer * (len(b) - 1) for b in result]
    for _ in range(12):
        best = None
        best_delta = -1.0
        for i, first in enumerate(result):
            for j in range(i + 1, len(result)):
                second = result[j]
                for a, p in enumerate(first):
                    for b, q in enumerate(second):
                        if (
                            loads[i] - stay(p) + stay(q) > minutes
                            or loads[j] - stay(q) + stay(p) > minutes
                        ):
                            continue
                        delta = sum(
                            distances[q.id, r.id] - distances[p.id, r.id]
                            for r in first
                            if r.id != p.id
                        )
                        delta += sum(
                            distances[p.id, r.id] - distances[q.id, r.id]
                            for r in second
                            if r.id != q.id
                        )
                        if delta < best_delta:
                            best, best_delta = (i, j, a, b), delta
        if best is None:
            break
        i, j, a, b = best
        p, q = result[i][a], result[j][b]
        result[i][a], result[j][b] = q, p
        loads[i] += stay(q) - stay(p)
        loads[j] += stay(p) - stay(q)
    for index, group in enumerate(result):
        ordered = group[:1]
        remaining = group[1:]
        while remaining:
            nearest = min(remaining, key=lambda p: distances[ordered[-1].id, p.id])
            remaining.remove(nearest)
            ordered.append(nearest)
        old = sum(distances[p.id, q.id] for p, q in zip(group, group[1:], strict=False))
        new = sum(distances[p.id, q.id] for p, q in zip(ordered, ordered[1:], strict=False))
        if new < old - 1:
            result[index] = ordered
    changed = [[p.id for p in b] for b in result] != [[p.id for p in b] for b in buckets]
    report = GeographyReport(
        applied=changed,
        reason="已按确认坐标减少同日分散或调整日内顺序；非实际路程，不保证最优或交通更快。"
        if changed
        else "已比较确认坐标，未找到符合容量约束的严格改善，保留原分日。",
        before_spread_meters=before,
        after_spread_meters=spread(result),
        groups=[
            GeographicGroup(
                day=i,
                places=[p.place_name for p in group],
                districts=list(
                    dict.fromkeys(points[p.id].district for p in group if points[p.id].district)
                ),
            )
            for i, group in enumerate(result, 1)
        ],
    )
    return result, report
